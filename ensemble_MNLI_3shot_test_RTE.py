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
from collections import defaultdict
from torch.utils.data import (DataLoader, RandomSampler, SequentialSampler,
                              TensorDataset)
from torch.utils.data.distributed import DistributedSampler
from tqdm import tqdm, trange

from torch.nn import CrossEntropyLoss, MSELoss
from scipy.special import softmax
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import matthews_corrcoef, f1_score



from pytorch_transformers.tokenization_roberta import RobertaTokenizer
from pytorch_transformers.optimization import AdamW
from pytorch_transformers.modeling_roberta import RobertaForSequenceClassification

from bert_common_functions import store_transformers_models

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)

# from pytorch_transformers.modeling_bert import BertPreTrainedModel, BertModel
# import torch.nn as nn



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

    def get_train_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the train set."""
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the dev set."""
        raise NotImplementedError()

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
    def get_MNLI_as_train(self, filename):
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
                text_a = line[8].strip()
                text_b = line[9].strip()
                label = line[-1].strip() #["entailment", "neutral", "contradiction"]
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
            # if line_co > 20000:
            #     break
        readfile.close()
        print('loaded  size:', line_co)
        return examples

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
                label = line[3].strip() #["entailment", "not_entailment"]
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
            line_co+=1
            if line_co > 20000:
                break
        readfile.close()
        print('loaded  size:', line_co)
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
                label = 'entailment'  if line[0] == '1' else 'neutral'
                examples.append(
                    InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
                line_co+=1

        readfile.close()
        print('loaded test size:', line_co)
        return examples

    def get_labels(self):
        'here we keep the three-way in MNLI training '
        return ["entailment", "neutral", "contradiction"]

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

        if ex_index < 5:
            logger.info("*** Example ***")
            logger.info("guid: %s" % (example.guid))
            logger.info("tokens: %s" % " ".join(
                    [str(x) for x in tokens]))
            logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
            logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
            logger.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
            logger.info("label: %s (id = %d)" % (example.label, label_id))

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










def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--data_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The input data dir. Should contain the .tsv files (or other data files) for the task.")
    parser.add_argument("--bert_model", default=None, type=str, required=True,
                        help="Bert pre-trained model selected in the list: bert-base-uncased, "
                        "bert-large-uncased, bert-base-cased, bert-large-cased, bert-base-multilingual-uncased, "
                        "bert-base-multilingual-cased, bert-base-chinese.")
    parser.add_argument("--task_name",
                        default=None,
                        type=str,
                        required=True,
                        help="The name of the task to train.")
    parser.add_argument("--output_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The output directory where the model predictions and checkpoints will be written.")

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
                        default=2e-5,
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

    label_list = processor.get_labels() #["entailment", "neutral", "contradiction"]
    num_labels = len(label_list)


    pretrain_model_dir = '/export/home/Dataset/BERT_pretrained_mine/crossdataentail/trainMNLItestRTE/0.8409469823274425' #'roberta-large' , 'roberta-large-mnli'
    model = RobertaForSequenceClassification.from_pretrained(pretrain_model_dir, num_labels=num_labels)
    tokenizer = RobertaTokenizer.from_pretrained(pretrain_model_dir, do_lower_case=args.do_lower_case)
    model.to(device)

    # pretrain_model_dir_2 = '/export/home/Dataset/BERT_pretrained_mine/crossdataentail/3shotRTE/0.8049349783261087' #'roberta-large' , 'roberta-large-mnli'
    pretrain_model_dir_2 = '/export/home/Dataset/BERT_pretrained_mine/crossdataentail/trainMNLI+3shotRTE_testRTE_2way/0.8332777592530843'
    model_2 = RobertaForSequenceClassification.from_pretrained(pretrain_model_dir_2, num_labels=num_labels-1)
    tokenizer_2 = RobertaTokenizer.from_pretrained(pretrain_model_dir_2, do_lower_case=args.do_lower_case)
    model_2.to(device)

    # # Prepare optimizer
    # param_optimizer = list(model.named_parameters())
    # no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
    # optimizer_grouped_parameters = [
    #     {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
    #     {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    #     ]
    #
    # optimizer = AdamW(optimizer_grouped_parameters,
    #                          lr=args.learning_rate)
    global_step = 0
    nb_tr_steps = 0
    tr_loss = 0
    max_test_acc = 0.0
    if args.do_train:
        '''load dev set'''
        eval_examples = processor.get_RTE_as_test('/export/home/Dataset/RTE/test_RTE_1235.txt')
        eval_features = convert_examples_to_features(
            eval_examples, label_list, args.max_seq_length, tokenizer, output_mode,
            cls_token_at_end=False,#bool(args.model_type in ['xlnet']),            # xlnet has a cls token at the end
            cls_token=tokenizer.cls_token,
            cls_token_segment_id=0,#2 if args.model_type in ['xlnet'] else 0,
            sep_token=tokenizer.sep_token,
            sep_token_extra=True,#bool(args.model_type in ['roberta']),           # roberta uses an extra separator b/w pairs of sentences, cf. github.com/pytorch/fairseq/commit/1684e166e3da03f5b600dbb7855cb98ddfcd0805
            pad_on_left=False,#bool(args.model_type in ['xlnet']),                 # pad on the left for xlnet
            pad_token=tokenizer.convert_tokens_to_ids([tokenizer.pad_token])[0],
            pad_token_segment_id=0)#4 if args.model_type in ['xlnet'] else 0,)

        eval_all_input_ids = torch.tensor([f.input_ids for f in eval_features], dtype=torch.long)
        eval_all_input_mask = torch.tensor([f.input_mask for f in eval_features], dtype=torch.long)
        eval_all_segment_ids = torch.tensor([f.segment_ids for f in eval_features], dtype=torch.long)
        eval_all_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.long)

        eval_data = TensorDataset(eval_all_input_ids, eval_all_input_mask, eval_all_segment_ids, eval_all_label_ids)
        eval_sampler = SequentialSampler(eval_data)
        eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=args.eval_batch_size)

        '''
        start evaluate on dev set after this epoch
        '''
        model_pred_list = []
        for model_id, model_eval in enumerate([model, model_2]):
            model_eval.eval()

            logger.info("***** Running evaluation *****")
            logger.info("  Num examples = %d", len(eval_examples))
            logger.info("  Batch size = %d", args.eval_batch_size)

            eval_loss = 0
            nb_eval_steps = 0
            preds = []
            gold_label_ids = []
            print('Evaluating...')
            for input_ids, input_mask, segment_ids, label_ids in eval_dataloader:
                input_ids = input_ids.to(device)
                input_mask = input_mask.to(device)
                segment_ids = segment_ids.to(device)
                label_ids = label_ids.to(device)
                gold_label_ids+=list(label_ids.detach().cpu().numpy())

                with torch.no_grad():
                    logits = model_eval(input_ids, None, input_mask, labels=None)
                logits = logits[0]

                # loss_fct = CrossEntropyLoss()
                # if model_id==0:
                #     tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                # else:


                # eval_loss += tmp_eval_loss.mean().item()
                # nb_eval_steps += 1
                if len(preds) == 0:
                    preds.append(logits.detach().cpu().numpy())
                else:
                    preds[0] = np.append(preds[0], logits.detach().cpu().numpy(), axis=0)

            # eval_loss = eval_loss / nb_eval_steps
            preds = preds[0]
            model_pred_list.append(preds)

            '''
            preds: size*3 ["entailment", "neutral", "contradiction"]
            wenpeng added a softxmax so that each row is a prob vec
            '''
            pred_probs = softmax(preds,axis=1)
            pred_indices = np.argmax(pred_probs, axis=1)
            pred_label_ids = []
            for p in pred_indices:
                pred_label_ids.append(0 if p == 0 else 1)
            gold_label_ids = gold_label_ids
            assert len(pred_label_ids) == len(gold_label_ids)
            hit_co = 0
            for k in range(len(pred_label_ids)):
                if pred_label_ids[k] == gold_label_ids[k]:
                    hit_co +=1
            test_acc = hit_co/len(gold_label_ids)
            print('test_acc:', test_acc)
        '''ensemble results'''
        pred_matrix_0 = model_pred_list[0]
        pred_matrix_1 = model_pred_list[1]
        pred_matrix_0[:,1] = np.max(pred_matrix_0[:,1:],axis=1)
        new_pred_matrix_0 = pred_matrix_0[:,:2]
        pred_probs_0 = softmax(new_pred_matrix_0,axis=1)
        pred_probs_1 = softmax(pred_matrix_1,axis=1)
        pred_indice_0 = np.argmax(pred_probs_0, axis=1)
        pred_indice_1 = np.argmax(pred_probs_1, axis=1)
        pred_label_ids = []
        for p in range(pred_probs_0.shape[0]):
            if pred_indice_0[p] == pred_indice_1[p]:
                pred_label_ids.append(pred_indice_0[p])
            elif pred_probs_0[p,pred_indice_0[p]] > pred_probs_1[p,pred_indice_1[p]]:
                pred_label_ids.append(pred_indice_0[p])
            else:
                pred_label_ids.append(pred_indice_1[p])


            # pred_label_ids.append(0 if p == 0 else 1)
        gold_label_ids = gold_label_ids
        assert len(pred_label_ids) == len(gold_label_ids)
        hit_co = 0
        for k in range(len(pred_label_ids)):
            if pred_label_ids[k] == gold_label_ids[k]:
                hit_co +=1
        test_acc = hit_co/len(gold_label_ids)
        print('test_acc:', test_acc)






if __name__ == "__main__":
    main()
# CUDA_VISIBLE_DEVICES=2 python -u train_RTE_test_RTE.py --task_name rte --do_train --do_lower_case --bert_model bert-large-uncased --learning_rate 2e-5 --num_train_epochs 3 --data_dir '' --output_dir '' > log.RTE.RTE.batch32.txt 2>&1
