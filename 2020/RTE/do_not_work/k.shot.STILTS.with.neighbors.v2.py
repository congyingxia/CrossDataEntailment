# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""BERT finetuning runner."""

from __future__ import absolute_import, division, print_function

import argparse
import csv
import logging
import os
import random
import sys
import codecs
import numpy as np
import torch
import torch.nn as nn
from collections import defaultdict
from torch.nn import functional as F
from torch.utils.data import (DataLoader, RandomSampler, SequentialSampler,
                              TensorDataset)
from torch.utils.data.distributed import DistributedSampler
from tqdm import tqdm, trange
from scipy.stats import beta
import operator
from torch.nn import CrossEntropyLoss, MSELoss
from scipy.special import softmax
# from scipy.stats import pearsonr, spearmanr
# from sklearn.metrics import matthews_corrcoef, f1_score

from transformers.tokenization_roberta import RobertaTokenizer
from transformers.optimization import AdamW
from transformers.modeling_roberta import RobertaModel#RobertaForSequenceClassification

# from transformers.modeling_bert import BertModel
# from transformers.tokenization_bert import BertTokenizer
# from bert_common_functions import store_transformers_models

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)

# from pytorch_transformers.modeling_bert import BertPreTrainedModel, BertModel
# import torch.nn as nn

bert_hidden_dim = 1024
pretrain_model_dir = 'roberta-large' #'roberta-large' , 'roberta-large-mnli', 'bert-large-uncased'

def store_transformers_models(model, tokenizer, output_dir, flag_str):
    '''
    store the model
    '''
    output_dir+='/'+flag_str
    # if not os.path.exists(output_dir):
    #     os.makedirs(output_dir)
    print('starting model storing....')
    # model.save_pretrained(output_dir)
    torch.save(model.state_dict(), output_dir)
    # tokenizer.save_pretrained(output_dir)
    print('store succeed')

class RobertaForSequenceClassification(nn.Module):
    def __init__(self, tagset_size):
        super(RobertaForSequenceClassification, self).__init__()
        self.tagset_size = tagset_size

        self.roberta_single= RobertaModel.from_pretrained(pretrain_model_dir)
        self.single_hidden2tag = RobertaClassificationHead(bert_hidden_dim, tagset_size)

    def forward(self, input_ids, input_mask):
        outputs_single = self.roberta_single(input_ids, input_mask, None)
        hidden_states_single = outputs_single[1]#torch.tanh(self.hidden_layer_2(torch.tanh(self.hidden_layer_1(outputs_single[1])))) #(batch, hidden)

        score_single, last_hidden = self.single_hidden2tag(hidden_states_single) #(batch, tag_set)
        return score_single, last_hidden



class RobertaClassificationHead(nn.Module):
    """wenpeng overwrite it so to accept matrix as input"""

    def __init__(self, bert_hidden_dim, num_labels):
        super(RobertaClassificationHead, self).__init__()
        self.dense = nn.Linear(bert_hidden_dim, bert_hidden_dim)
        self.dropout = nn.Dropout(0.1)
        self.out_proj = nn.Linear(bert_hidden_dim, num_labels)

    def forward(self, features):
        x = features#[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = self.dropout(x)
        x = self.dense(x)
        last_hidden = torch.tanh(x)
        x = self.dropout(last_hidden)
        x = self.out_proj(x)
        return x, last_hidden


class RobertaForSequenceClassification_TargetClassifier(nn.Module):
    def __init__(self, kshot, tagset_size):
        super(RobertaForSequenceClassification_TargetClassifier, self).__init__()
        '''
        kshot: means the size of the total support set
        '''
        self.tagset_size = tagset_size

        # self.roberta_single= RobertaModel.from_pretrained(pretrain_model_dir)
        # self.single_hidden2tag = RobertaClassificationHead(bert_hidden_dim, tagset_size)
        self.int_proj = nn.Linear(bert_hidden_dim, bert_hidden_dim+kshot)
        '''+1 means we consider the MNLI entail prob as one extra feature'''
        self.out_proj = nn.Linear(bert_hidden_dim+kshot+1, self.tagset_size)

    def forward(self, hidden_states_batch, prob_batch):
        '''
        hidden_states_batch: (batch, hidden)
        prob_batch: (batch, 1)
        '''
        layer1_hidden = torch.tanh(self.int_proj(hidden_states_batch))
        layer2_input = torch.cat([layer1_hidden, prob_batch], dim=1) #(batch, hidden+1)

        score_single = self.out_proj(layer2_input)
        return score_single

class InputExample(object):
    """A single training/test example for simple sequence classification."""

    def __init__(self, guid, text_a, text_b=None, label=None):
        """Constructs a InputExample.

        Args:
            guid: Unique id for the example.
            text_a: string. The untokenized text of the first sequence. For single
            sequence tasks, only this sequence must be specified.
            text_b: (Optional) string. The untokenized text of the second sequence.
            Only must be specified for sequence pair tasks.
            label: (Optional) string. The label of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text_a = text_a
        self.text_b = text_b
        self.label = label


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_id):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_id = label_id


class DataProcessor(object):
    """Base class for data converters for sequence classification data sets."""

    def get_labels(self):
        """Gets the list of labels for this data set."""
        raise NotImplementedError()

    @classmethod
    def _read_tsv(cls, input_file, quotechar=None):
        """Reads a tab separated value file."""
        with open(input_file, "r") as f:
            reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
            lines = []
            for line in reader:
                if sys.version_info[0] == 2:
                    line = list(unicode(cell, 'utf-8') for cell in line)
                lines.append(line)
            return lines

class RteProcessor(DataProcessor):
    """Processor for the RTE data set (GLUE version)."""


    def get_RTE_as_train(self, filename):
        '''
        can read the training file, dev and test file
        '''
        examples=[]
        readfile = codecs.open(filename, 'r', 'utf-8')
        line_co=0
        for row in readfile:
            if line_co>0:
                line=row.strip().split('\t')
                guid = "train-"+str(line_co-1)
                text_a = line[1].strip()
                text_b = line[2].strip()
                label = 'entailment' if line[3].strip()=='entailment' else 'not_entailment' #["entailment", "not_entailment"]
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
            # if line_co > 20000:
            #     break
        readfile.close()
        print('loaded  size:', line_co)
        return examples

    def get_RTE_as_train_k_shot(self, filename, k_shot):
        '''
        can read the training file, dev and test file
        '''
        examples_entail=[]
        examples_non_entail=[]
        readfile = codecs.open(filename, 'r', 'utf-8')
        line_co=0
        for row in readfile:
            if line_co>0:
                line=row.strip().split('\t')
                guid = "train-"+str(line_co-1)
                text_a = line[1].strip()
                text_b = line[2].strip()
                label = 'entailment' if line[3].strip()=='entailment' else 'not_entailment' #["entailment", "not_entailment"]
                if label == 'entailment':
                    examples_entail.append(
                        InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
                else:
                    examples_non_entail.append(
                        InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
        readfile.close()
        print('loaded  entail size:', len(examples_entail), 'non-entail size:', len(examples_non_entail))
        '''sampling'''
        if k_shot > 99999:
            return examples_entail+examples_non_entail
        else:
            sampled_examples = random.sample(examples_entail, k_shot)+random.sample(examples_non_entail, k_shot)
            return sampled_examples

    def get_RTE_as_dev(self, filename):
        '''
        can read the training file, dev and test file
        '''
        examples=[]
        readfile = codecs.open(filename, 'r', 'utf-8')
        line_co=0
        for row in readfile:
            if line_co>0:
                line=row.strip().split('\t')
                guid = "dev-"+str(line_co-1)
                text_a = line[1].strip()
                text_b = line[2].strip()
                # label = line[3].strip() #["entailment", "not_entailment"]
                label = 'entailment' if line[3].strip()=='entailment' else 'not_entailment'
                # label = 'entailment'  if line[3] == 'entailment' else 'neutral'
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
            # if line_co > 20000:
            #     break
        readfile.close()
        print('loaded  size:', line_co-1)
        return examples

    def get_RTE_as_test(self, filename):
        readfile = codecs.open(filename, 'r', 'utf-8')
        line_co=0
        examples=[]
        for row in readfile:
            line=row.strip().split('\t')
            if len(line)==3:
                guid = "test-"+str(line_co)
                text_a = line[1]
                text_b = line[2]
                '''for RTE, we currently only choose randomly two labels in the set, in prediction we then decide the predicted labels'''
                label = 'entailment'  if line[0] == '1' else 'not_entailment'
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
                line_co+=1

        readfile.close()
        print('loaded test size:', line_co)
        return examples
    def get_MNLI_train(self, filename):
        '''
        classes: ["entailment", "neutral", "contradiction"]
        '''

        examples=[]
        readfile = codecs.open(filename, 'r', 'utf-8')
        line_co=0
        for row in readfile:
            if line_co>0:
                line=row.strip().split('\t')
                guid = "train-"+str(line_co-1)
                # text_a = 'MNLI. '+line[8].strip()
                text_a = line[8].strip()
                text_b = line[9].strip()
                label = line[-1].strip() #["entailment", "neutral", "contradiction"]
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
        readfile.close()
        print('loaded  MNLI size:', len(examples))

        return examples #train, dev

    def get_labels(self):
        'here we keep the three-way in MNLI training '
        return ["entailment", "not_entailment"]
        # return ["entailment", "neutral", "contradiction"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples



def convert_examples_to_features(examples, label_list, max_seq_length,
                                 tokenizer, output_mode,
                                 cls_token_at_end=False,
                                 cls_token='[CLS]',
                                 cls_token_segment_id=1,
                                 sep_token='[SEP]',
                                 sep_token_extra=False,
                                 pad_on_left=False,
                                 pad_token=0,
                                 pad_token_segment_id=0,
                                 sequence_a_segment_id=0,
                                 sequence_b_segment_id=1,
                                 mask_padding_with_zero=True):
    """ Loads a data file into a list of `InputBatch`s
        `cls_token_at_end` define the location of the CLS token:
            - False (Default, BERT/XLM pattern): [CLS] + A + [SEP] + B + [SEP]
            - True (XLNet/GPT pattern): A + [SEP] + B + [SEP] + [CLS]
        `cls_token_segment_id` define the segment id associated to the CLS token (0 for BERT, 2 for XLNet)
    """

    label_map = {label : i for i, label in enumerate(label_list)}

    features = []
    for (ex_index, example) in enumerate(examples):
        if ex_index % 10000 == 0:
            logger.info("Writing example %d of %d" % (ex_index, len(examples)))

        tokens_a = tokenizer.tokenize(example.text_a)

        tokens_b = None
        if example.text_b:
            tokens_b = tokenizer.tokenize(example.text_b)
            # Modifies `tokens_a` and `tokens_b` in place so that the total
            # length is less than the specified length.
            # Account for [CLS], [SEP], [SEP] with "- 3". " -4" for RoBERTa.
            special_tokens_count = 4 if sep_token_extra else 3
            _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - special_tokens_count)
        else:
            # Account for [CLS] and [SEP] with "- 2" and with "- 3" for RoBERTa.
            special_tokens_count = 3 if sep_token_extra else 2
            if len(tokens_a) > max_seq_length - special_tokens_count:
                tokens_a = tokens_a[:(max_seq_length - special_tokens_count)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids:   0   0  0    0    0     0       0   0   1  1  1  1   1   1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids:   0   0   0   0  0     0   0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambiguously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens = tokens_a + [sep_token]
        if sep_token_extra:
            # roberta uses an extra separator b/w pairs of sentences
            tokens += [sep_token]
        segment_ids = [sequence_a_segment_id] * len(tokens)

        if tokens_b:
            tokens += tokens_b + [sep_token]
            segment_ids += [sequence_b_segment_id] * (len(tokens_b) + 1)

        if cls_token_at_end:
            tokens = tokens + [cls_token]
            segment_ids = segment_ids + [cls_token_segment_id]
        else:
            tokens = [cls_token] + tokens
            segment_ids = [cls_token_segment_id] + segment_ids

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1 if mask_padding_with_zero else 0] * len(input_ids)

        # Zero-pad up to the sequence length.
        padding_length = max_seq_length - len(input_ids)
        if pad_on_left:
            input_ids = ([pad_token] * padding_length) + input_ids
            input_mask = ([0 if mask_padding_with_zero else 1] * padding_length) + input_mask
            segment_ids = ([pad_token_segment_id] * padding_length) + segment_ids
        else:
            input_ids = input_ids + ([pad_token] * padding_length)
            input_mask = input_mask + ([0 if mask_padding_with_zero else 1] * padding_length)
            segment_ids = segment_ids + ([pad_token_segment_id] * padding_length)

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length

        if output_mode == "classification":
            label_id = label_map[example.label]
        elif output_mode == "regression":
            label_id = float(example.label)
        else:
            raise KeyError(output_mode)

        # if ex_index < 5:
        #     logger.info("*** Example ***")
        #     logger.info("guid: %s" % (example.guid))
        #     logger.info("tokens: %s" % " ".join(
        #             [str(x) for x in tokens]))
        #     logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
        #     logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
        #     logger.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
        #     logger.info("label: %s (id = %d)" % (example.label, label_id))

        features.append(
                InputFeatures(input_ids=input_ids,
                              input_mask=input_mask,
                              segment_ids=segment_ids,
                              label_id=label_id))
    return features

def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""

    # This is a simple heuristic which will always truncate the longer sequence
    # one token at a time. This makes more sense than truncating an equal percent
    # of tokens from each, since if one sequence is very short then each token
    # that's truncated likely contains more information than a longer sequence.
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()



def gram_set(example):
    target_text_a_wordlist = example.text_a.split()
    target_text_b_wordlist = example.text_b.split()
    unigram_set = set(target_text_a_wordlist+target_text_b_wordlist)
    bigram_set = set()
    for word_a in target_text_a_wordlist:
        for word_b in target_text_b_wordlist:
            bigram_set.add(word_a+'||'+word_b)
            bigram_set.add(word_b+'||'+word_a)

    return unigram_set |bigram_set #union of two sets

def retrieve_neighbors_source_given_kshot_target(target_examples, source_example_2_gramset, topN):

    returned_exs = []
    for i, target_ex in enumerate(target_examples):
        target_set = gram_set(target_ex)

        source_ex_2_score = {}
        j=0
        for source_ex, gramset in source_example_2_gramset.items():
            interset_gramset = target_set & gramset
            precision = len(interset_gramset)/len(gramset)
            # recall = len(interset_gramset)/len(target_set)
            score = precision#2*precision*recall/(1e-8+precision+recall)
            # if score > 0.2:
            source_ex_2_score[source_ex] = score
            j+=1
        sorted_d = sorted([(score, ex) for (ex,score) in source_ex_2_score.items()],key=operator.itemgetter(0), reverse=True)
        neighbor_exs = [ex for (score, ex) in sorted_d[:topN]]
        returned_exs+=neighbor_exs
    print('neighbor retrieve over')
    return returned_exs

def examples_to_features(source_examples, label_list, args, tokenizer, batch_size, output_mode, dataloader_mode='sequential'):
    source_features = convert_examples_to_features(
        source_examples, label_list, args.max_seq_length, tokenizer, output_mode,
        cls_token_at_end=False,#bool(args.model_type in ['xlnet']),            # xlnet has a cls token at the end
        cls_token=tokenizer.cls_token,
        cls_token_segment_id=0,#2 if args.model_type in ['xlnet'] else 0,
        sep_token=tokenizer.sep_token,
        sep_token_extra=True,#bool(args.model_type in ['roberta']),           # roberta uses an extra separator b/w pairs of sentences, cf. github.com/pytorch/fairseq/commit/1684e166e3da03f5b600dbb7855cb98ddfcd0805
        pad_on_left=False,#bool(args.model_type in ['xlnet']),                 # pad on the left for xlnet
        pad_token=tokenizer.convert_tokens_to_ids([tokenizer.pad_token])[0],
        pad_token_segment_id=0)#4 if args.model_type in ['xlnet'] else 0,)

    dev_all_input_ids = torch.tensor([f.input_ids for f in source_features], dtype=torch.long)
    dev_all_input_mask = torch.tensor([f.input_mask for f in source_features], dtype=torch.long)
    dev_all_segment_ids = torch.tensor([f.segment_ids for f in source_features], dtype=torch.long)
    dev_all_label_ids = torch.tensor([f.label_id for f in source_features], dtype=torch.long)

    dev_data = TensorDataset(dev_all_input_ids, dev_all_input_mask, dev_all_segment_ids, dev_all_label_ids)
    if dataloader_mode=='sequential':
        dev_sampler = SequentialSampler(dev_data)
    else:
        dev_sampler = RandomSampler(dev_data)
    dev_dataloader = DataLoader(dev_data, sampler=dev_sampler, batch_size=batch_size)


    return dev_dataloader



def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--task_name",
                        default=None,
                        type=str,
                        required=True,
                        help="The name of the task to train.")
    ## Other parameters
    parser.add_argument("--cache_dir",
                        default="",
                        type=str,
                        help="Where do you want to store the pre-trained models downloaded from s3")
    parser.add_argument("--max_seq_length",
                        default=128,
                        type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. \n"
                             "Sequences longer than this will be truncated, and sequences shorter \n"
                             "than this will be padded.")
    parser.add_argument("--do_train",
                        action='store_true',
                        help="Whether to run training.")

    parser.add_argument('--kshot',
                        type=int,
                        default=5,
                        help="random seed for initialization")
    parser.add_argument("--do_eval",
                        action='store_true',
                        help="Whether to run eval on the dev set.")
    parser.add_argument("--do_lower_case",
                        action='store_true',
                        help="Set this flag if you are using an uncased model.")
    parser.add_argument("--train_batch_size",
                        default=16,
                        type=int,
                        help="Total batch size for training.")
    parser.add_argument("--eval_batch_size",
                        default=64,
                        type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--learning_rate",
                        default=1e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--num_train_epochs",
                        default=3.0,
                        type=float,
                        help="Total number of training epochs to perform.")
    parser.add_argument("--warmup_proportion",
                        default=0.1,
                        type=float,
                        help="Proportion of training to perform linear learning rate warmup for. "
                             "E.g., 0.1 = 10%% of training.")
    parser.add_argument("--no_cuda",
                        action='store_true',
                        help="Whether not to use CUDA when available")
    parser.add_argument("--local_rank",
                        type=int,
                        default=-1,
                        help="local_rank for distributed training on gpus")
    parser.add_argument('--seed',
                        type=int,
                        default=42,
                        help="random seed for initialization")
    parser.add_argument('--neighbor_size_limit',
                        type=int,
                        default=500,
                        help="random seed for initialization")
    parser.add_argument('--gradient_accumulation_steps',
                        type=int,
                        default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument('--fp16',
                        action='store_true',
                        help="Whether to use 16-bit float precision instead of 32-bit")
    parser.add_argument('--loss_scale',
                        type=float, default=0,
                        help="Loss scaling to improve fp16 numeric stability. Only used when fp16 set to True.\n"
                             "0 (default value): dynamic loss scaling.\n"
                             "Positive power of 2: static loss scaling value.\n")
    parser.add_argument('--server_ip', type=str, default='', help="Can be used for distant debugging.")
    parser.add_argument('--server_port', type=str, default='', help="Can be used for distant debugging.")


    args = parser.parse_args()


    processors = {
        "rte": RteProcessor
    }

    output_modes = {
        "rte": "classification"
    }

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
        n_gpu = torch.cuda.device_count()
    else:
        torch.cuda.set_device(args.local_rank)
        device = torch.device("cuda", args.local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')
    logger.info("device: {} n_gpu: {}, distributed training: {}, 16-bits training: {}".format(
        device, n_gpu, bool(args.local_rank != -1), args.fp16))

    if args.gradient_accumulation_steps < 1:
        raise ValueError("Invalid gradient_accumulation_steps parameter: {}, should be >= 1".format(
                            args.gradient_accumulation_steps))

    args.train_batch_size = args.train_batch_size // args.gradient_accumulation_steps

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)

    if not args.do_train and not args.do_eval:
        raise ValueError("At least one of `do_train` or `do_eval` must be True.")


    task_name = args.task_name.lower()

    if task_name not in processors:
        raise ValueError("Task not found: %s" % (task_name))



    processor = processors[task_name]()
    output_mode = output_modes[task_name]


    train_examples = processor.get_RTE_as_train_k_shot('/export/home/Dataset/glue_data/RTE/train.tsv', args.kshot) #train_pu_half_v1.txt
    train_examples_MNLI = processor.get_MNLI_train('/export/home/Dataset/glue_data/MNLI/train.tsv')

    # source_example_2_gramset = {}
    # for mnli_ex in train_examples_MNLI:
    #     source_example_2_gramset[mnli_ex] = gram_set(mnli_ex)
    # print('MNLI gramset build over')
    # neighbor_size_limit = 500
    train_examples_neighbors = []#retrieve_neighbors_source_given_kshot_target(train_examples, source_example_2_gramset, args.neighbor_size_limit)
    # print('neighbor size:', len(train_examples_neighbors))
    # train_examples_neighbors_2way = []
    # for neighbor_ex in train_examples_neighbors:
    #     if neighbor_ex.label !='entailment':
    #         neighbor_ex.label = 'not_entailment'
    #     train_examples_neighbors_2way.append(neighbor_ex)


    dev_examples = processor.get_RTE_as_dev('/export/home/Dataset/glue_data/RTE/dev.tsv')
    test_examples = processor.get_RTE_as_test('/export/home/Dataset/RTE/test_RTE_1235.txt')
    label_list = ["entailment", "not_entailment"]
    mnli_label_list = ["entailment", "neutral", "contradiction"]
    # train_examples, dev_examples, test_examples, label_list = load_CLINC150_with_specific_domain_sequence(args.DomainName, args.kshot, augment=False)
    num_labels = len(label_list)
    print('num_labels:', num_labels, 'training size:', len(train_examples), 'neighbor size:', len(train_examples_neighbors),  'dev size:', len(dev_examples), 'test size:', len(test_examples))

    num_train_optimization_steps = None
    num_train_optimization_steps = int(
        len(train_examples) / args.train_batch_size / args.gradient_accumulation_steps) * args.num_train_epochs
    if args.local_rank != -1:
        num_train_optimization_steps = num_train_optimization_steps // torch.distributed.get_world_size()

    model = RobertaForSequenceClassification(3)
    tokenizer = RobertaTokenizer.from_pretrained(pretrain_model_dir, do_lower_case=args.do_lower_case)
    model.load_state_dict(torch.load('/export/home/Dataset/BERT_pretrained_mine/MNLI_pretrained/_acc_0.9040886899918633.pt'))
    # model.load_state_dict(torch.load('/export/home/Dataset/BERT_pretrained_mine/MNLI_biased_pretrained/RTE.10shot.seed.42.pt'))
    model.to(device)


    target_model = RobertaForSequenceClassification_TargetClassifier(args.kshot*num_labels, 3)
    target_model.to(device)


    param_optimizer = list(model.named_parameters())
    no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
        {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
        ]

    optimizer = AdamW(optimizer_grouped_parameters,
                             lr=5e-7)

    param_optimizer_target = list(target_model.named_parameters())
    no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
    optimizer_grouped_parameters_target = [
        {'params': [p for n, p in param_optimizer_target if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
        {'params': [p for n, p in param_optimizer_target if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
        ]

    optimizer_target = AdamW(optimizer_grouped_parameters_target,
                             lr=args.learning_rate)

    global_step = 0
    nb_tr_steps = 0
    tr_loss = 0
    max_test_acc = 0.0
    max_dev_acc = 0.0
    if args.do_train:
        train_dataloader = examples_to_features(train_examples, label_list, args, tokenizer, args.train_batch_size, "classification", dataloader_mode='random')
        # train_neighbors_dataloader = examples_to_features(train_examples_neighbors, mnli_label_list, args, tokenizer, 5, "classification", dataloader_mode='random')
        dev_dataloader = examples_to_features(dev_examples, label_list, args, tokenizer, args.eval_batch_size, "classification", dataloader_mode='sequential')
        test_dataloader = examples_to_features(test_examples, label_list, args, tokenizer, args.eval_batch_size, "classification", dataloader_mode='sequential')
        # train_mnli_dataloader = examples_to_features(train_examples_MNLI, mnli_label_list, args, tokenizer, 32, "classification", dataloader_mode='random')

        # '''first pretrain on neighbors'''
        # iter_co = 0
        # for _ in trange(int(args.num_train_epochs), desc="Epoch"):
        #     tr_loss = 0
        #     nb_tr_examples, nb_tr_steps = 0, 0
        #     for step, batch in enumerate(tqdm(train_neighbors_dataloader, desc="Iteration")):
        #         model.train()
        #         batch = tuple(t.to(device) for t in batch)
        #         input_ids, input_mask, segment_ids, label_ids = batch
        #
        #
        #         logits,_ = model(input_ids, input_mask)
        #         loss_fct = CrossEntropyLoss()
        #
        #         loss = loss_fct(logits.view(-1, len(mnli_label_list)), label_ids.view(-1))
        #         if n_gpu > 1:
        #             loss = loss.mean() # mean() to average on multi-gpu.
        #         if args.gradient_accumulation_steps > 1:
        #             loss = loss / args.gradient_accumulation_steps
        #
        #         loss.backward()
        #
        #         tr_loss += loss.item()
        #         nb_tr_examples += input_ids.size(0)
        #         nb_tr_steps += 1
        #
        #         optimizer.step()
        #         optimizer.zero_grad()
        #         global_step += 1
        #         iter_co+=1
        #
        #     '''
        #     start evaluate on dev set after this epoch
        #     '''
        #     model.eval()
        #
        #
        #     eval_loss = 0
        #     nb_eval_steps = 0
        #     preds = []
        #     gold_label_ids = []
        #     # print('Evaluating...')
        #     for input_ids, input_mask, segment_ids, label_ids in dev_dataloader:
        #         input_ids = input_ids.to(device)
        #         input_mask = input_mask.to(device)
        #         segment_ids = segment_ids.to(device)
        #         label_ids = label_ids.to(device)
        #         gold_label_ids+=list(label_ids.detach().cpu().numpy())
        #
        #         with torch.no_grad():
        #             logits,_ = model(input_ids, input_mask)
        #         if len(preds) == 0:
        #             preds.append(logits.detach().cpu().numpy())
        #         else:
        #             preds[0] = np.append(preds[0], logits.detach().cpu().numpy(), axis=0)
        #
        #     preds = preds[0]
        #
        #     pred_probs = softmax(preds,axis=1)
        #     pred_label_ids_3way = list(np.argmax(pred_probs, axis=1))
        #     '''change from 3-way to 2-way'''
        #     pred_label_ids = []
        #     for pred_id in pred_label_ids_3way:
        #         if pred_id !=0:
        #             pred_label_ids.append(1)
        #         else:
        #             pred_label_ids.append(0)
        #
        #     gold_label_ids = gold_label_ids
        #     assert len(pred_label_ids) == len(gold_label_ids)
        #     hit_co = 0
        #     for k in range(len(pred_label_ids)):
        #         if pred_label_ids[k] == gold_label_ids[k]:
        #             hit_co +=1
        #     test_acc = hit_co/len(gold_label_ids)
        #
        #     if test_acc > max_dev_acc:
        #         max_dev_acc = test_acc
        #         print('\ndev acc:', test_acc, ' max_dev_acc:', max_dev_acc, '\n')
        #         '''store the model, because we can test after a max_dev acc reached'''
        #         model_to_save = (
        #             model.module if hasattr(model, "module") else model
        #         )  # Take care of distributed/parallel training
        #         store_transformers_models(model_to_save, tokenizer, '/export/home/Dataset/BERT_pretrained_mine/MNLI_biased_pretrained', 'dev_v2_seed_'+str(args.seed)+'_acc_'+str(max_dev_acc)+'.pt')
        #     else:
        #         print('\ndev acc:', test_acc, ' max_dev_acc:', max_dev_acc, '\n')
        #
        #
        # '''use MNLI to pretrain the target classifier'''
        # model.load_state_dict(torch.load('/export/home/Dataset/BERT_pretrained_mine/MNLI_biased_pretrained/'+'dev_v2_seed_'+str(args.seed)+'_acc_'+str(max_dev_acc)+'.pt'))
        # for _ in trange(3, desc="Epoch"):
        #     tr_loss = 0
        #     nb_tr_examples, nb_tr_steps = 0, 0
        #     for step, batch in enumerate(tqdm(train_mnli_dataloader, desc="Iteration")):
        #
        #         batch = tuple(t.to(device) for t in batch)
        #         input_ids, input_mask, segment_ids, label_ids = batch
        #         '''first get the rep'''
        #         model.eval()
        #         with torch.no_grad():
        #             logits, last_hidden = model(input_ids, input_mask)
        #         prob_of_entail = F.log_softmax(logits.view(-1, 3), dim=1)[:,:1] #(batch, 1)
        #
        #         target_model.train()
        #         target_logits = target_model(last_hidden, prob_of_entail)
        #         loss_fct = CrossEntropyLoss()
        #
        #         loss = loss_fct(target_logits.view(-1, len(mnli_label_list)), label_ids.view(-1))
        #         if n_gpu > 1:
        #             loss = loss.mean() # mean() to average on multi-gpu.
        #         if args.gradient_accumulation_steps > 1:
        #             loss = loss / args.gradient_accumulation_steps
        #
        #         loss.backward()
        #
        #         tr_loss += loss.item()
        #         nb_tr_examples += input_ids.size(0)
        #         nb_tr_steps += 1
        #
        #         optimizer_target.step()
        #         optimizer_target.zero_grad()

        '''fine-tune on kshot'''

        # model.load_state_dict(torch.load('/export/home/Dataset/BERT_pretrained_mine/MNLI_biased_pretrained/'+'dev_seed_'+str(args.seed)+'_acc_'+str(max_dev_acc)+'.pt'))
        iter_co = 0
        max_dev_acc=0.0
        final_test_performance = 0.0
        for _ in trange(int(args.num_train_epochs), desc="Epoch"):
            tr_loss = 0
            nb_tr_examples, nb_tr_steps = 0, 0
            for step, batch in enumerate(tqdm(train_dataloader, desc="Iteration")):
                # model.train()
                batch = tuple(t.to(device) for t in batch)
                input_ids, input_mask, segment_ids, label_ids = batch

                '''first get the rep'''
                model.eval()
                with torch.no_grad():
                    logits, last_hidden = model(input_ids, input_mask)
                source_prob = F.log_softmax(logits.view(-1, 3), dim=1)
                prob_of_entail = source_prob[:,:1] #(batch, 1)

                target_model.train()
                target_logits = target_model(last_hidden, prob_of_entail)


                prob_matrix = F.log_softmax((target_logits).view(-1, 3), dim=1)
                # prob_matrix = F.log_softmax(target_prob_matrix+source_prob, dim=1)

                '''this step *1.0 is very important, otherwise bug'''
                new_prob_matrix = prob_matrix*1.0
                '''change the entail prob to p or 1-p'''
                changed_places = torch.nonzero(label_ids.view(-1), as_tuple=False)
                new_prob_matrix[changed_places, 0] = 1.0 - prob_matrix[changed_places, 0]

                loss = F.nll_loss(new_prob_matrix, torch.zeros_like(label_ids).to(device).view(-1))

                if n_gpu > 1:
                    loss = loss.mean() # mean() to average on multi-gpu.
                if args.gradient_accumulation_steps > 1:
                    loss = loss / args.gradient_accumulation_steps

                loss.backward()

                tr_loss += loss.item()
                nb_tr_examples += input_ids.size(0)
                nb_tr_steps += 1

                optimizer_target.step()
                optimizer_target.zero_grad()
                global_step += 1
                iter_co+=1
                # if iter_co %20==0:
                if iter_co % len(train_dataloader)==0:
                    '''
                    start evaluate on dev set after this epoch
                    '''
                    model.eval()
                    target_model.eval()

                    for idd, dev_or_test_dataloader in enumerate([dev_dataloader, test_dataloader]):


                        if idd == 0:
                            logger.info("***** Running dev *****")
                            logger.info("  Num examples = %d", len(dev_examples))
                        else:
                            logger.info("***** Running test *****")
                            logger.info("  Num examples = %d", len(test_examples))
                        # logger.info("  Batch size = %d", args.eval_batch_size)

                        eval_loss = 0
                        nb_eval_steps = 0
                        preds = []
                        gold_label_ids = []
                        # print('Evaluating...')
                        for input_ids, input_mask, segment_ids, label_ids in dev_or_test_dataloader:
                            input_ids = input_ids.to(device)
                            input_mask = input_mask.to(device)
                            segment_ids = segment_ids.to(device)
                            label_ids = label_ids.to(device)
                            gold_label_ids+=list(label_ids.detach().cpu().numpy())

                            with torch.no_grad():
                                source_logits, last_hidden = model(input_ids, input_mask)

                            source_prob = F.log_softmax(source_logits.view(-1, 3), dim=1)
                            prob_of_entail = source_prob[:,:1] #(batch, 1)
                            with torch.no_grad():
                                target_logits = target_model(last_hidden, prob_of_entail)

                            logits = F.log_softmax(target_logits, dim=1)+source_prob
                            if len(preds) == 0:
                                preds.append(logits.detach().cpu().numpy())
                            else:
                                preds[0] = np.append(preds[0], logits.detach().cpu().numpy(), axis=0)

                        preds = preds[0]

                        pred_probs = softmax(preds,axis=1)
                        pred_label_ids_3way = list(np.argmax(pred_probs, axis=1))
                        '''change from 3-way to 2-way'''
                        pred_label_ids = []
                        for pred_id in pred_label_ids_3way:
                            if pred_id !=0:
                                pred_label_ids.append(1)
                            else:
                                pred_label_ids.append(0)

                        gold_label_ids = gold_label_ids
                        assert len(pred_label_ids) == len(gold_label_ids)
                        hit_co = 0
                        for k in range(len(pred_label_ids)):
                            if pred_label_ids[k] == gold_label_ids[k]:
                                hit_co +=1
                        test_acc = hit_co/len(gold_label_ids)

                        if idd == 0: # this is dev
                            if test_acc > max_dev_acc:
                                max_dev_acc = test_acc
                                print('\ndev acc:', test_acc, ' max_dev_acc:', max_dev_acc, '\n')

                            else:
                                print('\ndev acc:', test_acc, ' max_dev_acc:', max_dev_acc, '\n')
                                break
                        else: # this is test
                            if test_acc > max_test_acc:
                                max_test_acc = test_acc

                            final_test_performance = test_acc
                            print('\ntest acc:', test_acc, ' max_test_acc:', max_test_acc, '\n')
        print('final_test_performance:', final_test_performance)



if __name__ == "__main__":
    main()


'''

CUDA_VISIBLE_DEVICES=4 python -u k.shot.STILTS.with.neighbors.v2.py --task_name rte --do_train --do_lower_case --num_train_epochs 20 --train_batch_size 2 --eval_batch_size 32 --learning_rate 1e-6 --max_seq_length 128 --seed 42 --kshot 10 --neighbor_size_limit 500


'''
