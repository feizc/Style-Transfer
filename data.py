import random
import torch
import numpy as np
import os

PAD, UNK, BOS, EOS = '<pad>', '<unk>', '<bos>', '<eos>'
LS, RS, SP = '<s>', '</s>', ' '
BUFSIZE = 4096000

def ListsToTensor(xs, vocab=None):
    max_len = max(len(x) for x in xs)
    ys = []
    for x in xs:
        if vocab is not None:
            y = vocab.token2idx(x) + [vocab.padding_idx]*(max_len - len(x))
        else:
            y = x + [0]*(max_len - len(x))
        ys.append(y)
    return ys

def _back_to_text_for_check(x, vocab):
    w = x.t().tolist()
    for sent in vocab.idx2token(w):
        print (' '.join(sent))
    
def batchify(pre, retri, tar, vocab):
    # 将 3 类句子拼起来作为输入
    data = []
    tar_len = []
    for i in range(len(pre)):
        tar_len.append(len(pre[i].strip().split())+len(retri[i].strip().split()))
        # print(len(tar[i].strip().split()))
        data.append((pre[i].strip()+retri[i].strip()+tar[i].strip()).split())

    #print(data)
    truth, inp, msk = [], [], []
    for x in data:
        inp.append(x[:-1])
        truth.append(x[1:])
        msk.append([1 for i in range(len(x) -1)])
    truth = torch.LongTensor(ListsToTensor(truth, vocab)).t_().contiguous()
    inp = torch.LongTensor(ListsToTensor(inp, vocab)).t_().contiguous()
    msk = torch.FloatTensor(ListsToTensor(msk))

    # 将作为条件的句子mask掉
    for i in range(len(tar_len)):
        for j in range(tar_len[i]):
            msk[i][j] = 0.
    msk = msk.t_().contiguous()
    # tar_len = torch.LongTensor(tar_len).t_().contiguous()
    return truth, inp, msk

def s2t(strs, vocab):
    inp, msk = [], []
    for x in strs:
        inp.append([w for w in x])
        msk.append([1 for i in range(len(x))])

    inp = torch.LongTensor(ListsToTensor(inp, vocab)).t_().contiguous()
    msk = torch.FloatTensor(ListsToTensor(msk)).t_().contiguous()
    return inp, msk

class DataLoader(object):
    def __init__(self, vocab, filename, batch_size, max_len, min_len):
        self.batch_size = batch_size
        self.vocab = vocab
        self.max_len = max_len
        self.min_len = min_len
        self.filename = filename
        #self.stream = open(self.filename, encoding='utf8')
        # 前两句话 + 检索到的2句话 -> 下一句话
        self.previous = open(os.path.join(self.filename, 'previous.txt'), encoding='utf-8')
        self.retrieval = open(os.path.join(self.filename, 'retrieval.txt'), encoding='utf-8')
        self.target = open(os.path.join(self.filename, 'target.txt'), encoding='utf-8')
        self.epoch_id = 0

    def __iter__(self):
        
        # lines = self.stream.readlines(BUFSIZE)
        pre_lines = self.previous.readlines(BUFSIZE)
        retri_lines = self.retrieval.readlines(BUFSIZE)
        tar_lines = self.target.readlines(BUFSIZE)

        if not pre_lines:
            self.epoch_id += 1
            # self.stream.close()
            self.previous.close()
            self.retrieval.close()
            self.target.close()
            # self.stream = open(self.filename, encoding='utf8')
            self.previous = open(os.path.join(self.filename, 'previous.txt'), encoding='utf-8')
            self.retrieval = open(os.path.join(self.filename, 'retrieval.txt'), encoding='utf-8')
            self.target = open(os.path.join(self.filename, 'target.txt'), encoding='utf-8')
            # lines = self.stream.readlines(BUFSIZE)
            pre_lines = self.previous.readlines(BUFSIZE)
            retri_lines = self.retrieval.readlines(BUFSIZE)
            tar_lines = self.target.readlines(BUFSIZE)

        idx = 0
        while idx < len(pre_lines):
            yield batchify(pre_lines[idx:idx+self.batch_size], retri_lines[idx:idx+self.batch_size], tar_lines[idx:idx+self.batch_size], self.vocab)
            idx += self.batch_size

class Vocab(object):
    def __init__(self, filename, min_occur_cnt, specials = None):
        idx2token = [PAD, UNK, BOS, EOS] + [LS, RS, SP] + (specials if specials is not None else [])
        for line in open(filename, encoding='utf8').readlines():
            try: 
                token, cnt = line.strip().split()
            except:
                continue
            if int(cnt) >= min_occur_cnt:
                idx2token.append(token)
        self._token2idx = dict(zip(idx2token, range(len(idx2token))))
        self._idx2token = idx2token
        self._padding_idx = self._token2idx[PAD]
        self._unk_idx = self._token2idx[UNK]

    @property
    def size(self):
        return len(self._idx2token)
    
    @property
    def unk_idx(self):
        return self._unk_idx
    
    @property
    def padding_idx(self):
        return self._padding_idx
    
    def random_token(self):
        return self.idx2token(1 + np.random.randint(self.size-1))

    def idx2token(self, x):
        if isinstance(x, list):
            return [self.idx2token(i) for i in x]
        return self._idx2token[x]

    def token2idx(self, x):
        if isinstance(x, list):
            return [self.token2idx(i) for i in x]
        return self._token2idx.get(x, self.unk_idx)


'''
        # 这部分数据处理放入到采样的时候
        data = []
        for line in lines[:-1]: #the last sent may be imcomplete
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if len(tokens) > self.max_len:
                tokens = tokens[:self.max_len]
            if len(tokens) >= self.min_len:
                data.append(tokens)
        random.shuffle(data)
'''
