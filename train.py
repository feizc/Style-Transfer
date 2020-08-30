# coding=utf-8
import torch
import torch.distributed as dist
import torch.nn as nn
import torch.nn.functional as F
import torch.multiprocessing as mp

from biglm import BIGLM
from data import Vocab, DataLoader
from adam import AdamWeightDecayOptimizer
from optim import Optim

import argparse, os
import random

def parse_config():
    parser = argparse.ArgumentParser()
    parser.add_argument('--embed_dim', type=int, default=768)
    parser.add_argument('--ff_embed_dim', type=int, default=3072)
    parser.add_argument('--num_heads', type=int, default=12)
    parser.add_argument('--layers', type=int, default=12)
    parser.add_argument('--dropout', type=float, default=0.2)

    parser.add_argument('--train_data', type=str, default='./data')
    parser.add_argument('--vocab', type=str, default='./data/zh117M-200G/vocab.txt')
    #parser.add_argument('--min_occur_cnt', type=int, defalut=0)

    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--warmup_steps', type=int, default=10000)
    parser.add_argument('--lr', type=float, default=1)
    parser.add_argument('--smoothing', type=float, default=0.1)
    parser.add_argument('--weight_decay', type=float, default=0)
    parser.add_argument('--max_len', type=int, default=256)
    parser.add_argument('--min_len', type=int, default=1)
    parser.add_argument('--print_every', type=int, default=100)
    parser.add_argument('--save_every', type=int, default=5000)
    parser.add_argument('--start_from', type=str, default='./data/zh117M-200G/zh_117M.ckpt')
    parser.add_argument('--save_dir', type=str, default='./model')

    parser.add_argument('--approx', type=str, default='none')
    parser.add_argument('--fp16', action='store_true')
    parser.add_argument('--world_size', type=int, default=4)
    parser.add_argument('--gpus', type=int, default=4)
    parser.add_argument('--MASTER_ADDR', type=str, default="localhost")
    parser.add_argument('--MASTER_PORT', type=str, default="28512")
    parser.add_argument('--start_rank', type=int, default=0)
    parser.add_argument('--backend', type=str, default="nccl")

    return parser.parse_args()

def update_lr(optimizer, lr):
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr
 
def average_gradients(model):
    """ Gradient averaging. """
    normal = True
    size = float(dist.get_world_size())
    for param in model.parameters():
        if param.grad is not None:
            dist.all_reduce(param.grad.data, op=dist.ReduceOp.SUM)
            param.grad.data /= size
        else:
            normal = False
            break
    return normal

def run(args, local_rank):
    """ Distributed Synchronous """
    torch.manual_seed(1234)
    vocab = Vocab(args.vocab, min_occur_cnt=1, specials=[])
    if (args.world_size == 1 or dist.get_rank() == 0):
        print ("vocab.size = %d"%vocab.size, flush=True)
    model = BIGLM(local_rank, vocab, args.embed_dim, args.ff_embed_dim,\
                  args.num_heads, args.dropout, args.layers, args.smoothing, args.approx)
    if args.start_from is not None:
        ckpt = torch.load(args.start_from, map_location='cpu')
        model.load_state_dict(ckpt['model'])
    model = model.cuda(local_rank)
   
    if args.world_size > 1:
        torch.manual_seed(1234 + dist.get_rank())
        random.seed(5678 + dist.get_rank())
    
    optimizer = Optim(model.embed_dim, args.lr, args.warmup_steps, torch.optim.Adam(model.parameters(), lr=0, betas=(0.9, 0.998), eps=1e-9))

    #if args.start_from is not None:
    #    optimizer.load_state_dict(ckpt['optimizer'])

    #train_data = DataLoader(vocab, args.train_data+"0"+str(local_rank), args.batch_size, args.max_len, args.min_len)
    train_data = DataLoader(vocab, args.train_data, args.batch_size, args.max_len, args.min_len)
    batch_acm = 0
    acc_acm, nll_acm, ppl_acm, ntokens_acm, nxs, npairs_acm, loss_acm = 0., 0., 0., 0., 0., 0., 0.
    while True:
        model.train()
        for truth, inp, msk in train_data:
            batch_acm += 1
            truth = truth.cuda(local_rank)
            inp = inp.cuda(local_rank)
            msk = msk.cuda(local_rank)

            model.zero_grad()
            res, loss, acc, nll, ppl, ntokens, npairs = model(truth, inp, msk)
            loss_acm += loss.item()
            acc_acm += acc
            nll_acm += nll
            ppl_acm += ppl
            ntokens_acm += ntokens
            npairs_acm += npairs
            nxs += npairs
            
            loss.backward()
            if args.world_size > 1:
                average_gradients(model)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            if (args.world_size==1 or dist.get_rank() ==0) and batch_acm%args.print_every == -1%args.print_every:
                print ('batch_acm %d, loss %.3f, acc %.3f, nll %.3f, ppl %.3f, x_acm %d, lr %.6f'\
                        %(batch_acm, loss_acm/args.print_every, acc_acm/ntokens_acm, \
                        nll_acm/nxs, ppl_acm/nxs, npairs_acm, optimizer._rate), flush=True)
                acc_acm, nll_acm, ppl_acm, ntokens_acm, loss_acm, nxs = 0., 0., 0., 0., 0., 0.
            if (args.world_size==1 or dist.get_rank() ==0) and batch_acm%args.save_every == -1%args.save_every:
                if not os.path.exists(args.save_dir):
                    os.mkdir(args.save_dir)
                torch.save({'args':args, 'model':model.state_dict(), 'optimizer':optimizer.state_dict()}, '%s/epoch%d_batch_%d'%(args.save_dir, train_data.epoch_id, batch_acm))

def init_processes(args, local_rank, fn, backend='nccl'):
    """ Initialize the distributed environment. """
    os.environ['MASTER_ADDR'] = args.MASTER_ADDR
    os.environ['MASTER_PORT'] = args.MASTER_PORT
    dist.init_process_group(backend, rank=args.start_rank + local_rank, world_size=args.world_size)
    fn(args, local_rank)

if __name__ == "__main__":
    mp.set_start_method('spawn')
    args = parse_config()
    if args.world_size == 1:
        run(args, 0)
        exit(0)
    processes = []
    for rank in range(args.gpus):
        p = mp.Process(target=init_processes, args=(args, rank, run, args.backend))
        p.start()
        processes.append(p)
    for p in processes:
        p.join()
