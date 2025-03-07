WeChat: cstutorcs
QQ: 749389476
Email: tutorcs@163.com


from __future__ import annotations

"""Handling the input and output of the Neural Dependency Model"""

import os, sys
import re
import typing as T
import xml.etree.ElementTree as ET
from enum import IntFlag, auto
from multiprocessing import Pool, cpu_count
from gzip import open as gz_open
from itertools import islice
from pathlib import Path
from pickle import dump, load
from sys import stdout

from nltk.corpus.reader.api import SyntaxCorpusReader
from nltk.corpus.reader.util import read_blankline_block
from nltk.corpus.util import LazyCorpusLoader
from nltk.data import path
from nltk.parse import DependencyGraph
import numpy as np
from tqdm import tqdm

from torch.utils.data import Dataset

from conllu import parse_token_and_metadata

from q2_algorithm import is_projective
from q1_parse import PartialParse, get_sentence

# Q1 Data

class UniversalDependencyCorpusReader(SyntaxCorpusReader):
    """Update to DependencyCorpusReader to account for 10-field conllu fmt"""

    def _read_block(self, stream):
        sent_block = read_blankline_block(stream)
        if not sent_block:
            return sent_block
        lines_w_comments = sent_block[0].split('\n')
        lines_wo_comments = (line.strip() for line in lines_w_comments
                             if line and line[0] != '#'
                             )
        field_block = (line.split('\t') for line in lines_wo_comments)
        # need to kill lines that represent contractions. Their first
        # field is a range (e.g. 1-2)
        field_block = (
            fields for fields in field_block if '-' not in fields[0])
        # "blocks" are lists of sentences, so return our generator
        # encapsulated
        return [field_block]

    def _word(self, s):
        return [fields[1] for fields in s]

    def _tag(self, s, _):
        return [(fields[1], fields[3]) for fields in s]

    def _parse(self, s):
        # dependencygraph wants it all back together...
        block = '\n'.join('\t'.join(line) for line in s)
        return DependencyGraph(block, top_relation_label='root')

class Transducer(object):
    """Provides generator methods for converting between data types

    Args:
        word_list : an ordered list of words in the corpus. word_list[i]
            will be assigned the id = i + 1. root is assigned id 0 and
            any words not in the list are assigned id = len(word_list) + 2.
            id = len(word_list) + 3 is assigned to invalid
        tag_list : an ordered list of part-of-speech tags in the corpus.
            ids assigned as with word_list
        deprel_list : an ordered list of dendency relations. ids
            assigned as with word_list
    """

    root_word = None
    """Placeholder for the root word"""

    unk_word = '_'
    """Placeholder for unknown words"""

    root_tag = 'TOP'
    """POS tag assigned to root node"""

    unk_tag = '_'
    """POS tag assigned to unknown words"""

    root_deprel = 'ROOT'
    """Dependency relation btw root node and head word"""

    unk_deprel = '_'
    """Unknown dependency relation"""

    def __init__(self, word_list, tag_list, deprel_list):
        self.id2word = (self.root_word,) + tuple(word_list)
        self.id2word += (self.unk_word,)
        self.id2tag = (self.root_tag,) + tuple(tag_list)
        self.id2tag += (self.unk_tag,)
        self.id2deprel = (self.root_deprel,) + tuple(deprel_list)
        self.id2deprel += (self.unk_deprel,)
        self.word2id = dict((val, idx) for idx, val in enumerate(self.id2word))
        self.tag2id = dict((val, idx) for idx, val in enumerate(self.id2tag))
        self.deprel2id = dict(
            (val, idx) for idx, val in enumerate(self.id2deprel))
        self.unk_word_id = len(self.id2word) - 1
        self.unk_tag_id = len(self.id2tag) - 1
        self.unk_deprel_id = len(self.id2deprel) - 1
        self.null_word_id = len(self.id2word)
        self.null_tag_id = len(self.id2tag)
        self.null_deprel_id = len(self.id2deprel)

    def graph2id(self, graph):
        """Generate ID quads (word, tag, head, deprel) from single graph"""
        yield 0, 0, self.null_word_id, self.null_deprel_id
        for node_address in range(1, len(graph.nodes)):
            node = graph.nodes[node_address]
            yield (
                self.word2id.get(node['word'], self.unk_word_id),
                self.tag2id.get(node['ctag'], self.unk_tag_id),
                self.word2id.get(
                    graph.nodes[node['head']]['word'], self.unk_word_id),
                self.deprel2id.get(node['rel'], self.unk_deprel_id),
                )

    def graph2arc(self, graph, include_deprel=True):
        """Generate (head_idx, dep_idx, deprel_id) tuples from single graph

        Args:
            include_deprel : whether to include the dependency label
        """
        for node_address in range(1, len(graph.nodes)):
            node = graph.nodes[node_address]
            if include_deprel:
                yield (node['head'], node_address,
                       self.deprel2id.get(node['rel'], self.unk_deprel_id))
            else:
                yield (node['head'], node_address)

    def pp2feat(self, pp):
        """From a PartialParse, construct a feature vector triple

        The triple can be fed in as word, pos, and deprel inputs to the
        transducer, respectively. They are formed as follows:

        word/tag vectors (18 each):
            - top 3 ids on stack
            - top 3 ids on buffer
            - 1st and 2nd leftmost and rightmost dependants from top
              two words on stack (8)
            - leftmost-leftmost and rightmost-rightmost of top two words
              on stack (4)

        deprel vector (12):
            - 1st and 2nd leftmost and rightmost dependants from top
              two words on stack (8)
            - leftmost-leftmost and rightmost-rightmost of top two words
              on stack (4)

        Returns:
            word_ids, tag_ids, deprel_ids
        """
        word_ids = np.ones(18, dtype=np.int64) * self.null_word_id
        tag_ids = np.ones(18, dtype=np.int64) * self.null_tag_id
        deprel_ids = np.ones(12, dtype=np.int64) * self.null_deprel_id
        for stack_idx in range(min(3, len(pp.stack))):
            sentence_idx = pp.stack[-1 - stack_idx]
            word, tag = pp.sentence[sentence_idx]
            word_ids[stack_idx] = self.word2id.get(word, self.unk_word_id)
            tag_ids[stack_idx] = self.tag2id.get(tag, self.unk_tag_id)
            if stack_idx == 2:
                continue
            # first 2 leftmost
            for l_idx, l_dep in enumerate(
                    pp.get_nleftmost(sentence_idx, n=2)):
                word, tag = pp.sentence[l_dep]
                # should only be one that matches this
                deprel = next(arc[2] for arc in pp.arcs if arc[1] == l_dep)
                word_ids[6 + l_idx + 2 * stack_idx] = self.word2id.get(
                    word, self.unk_word_id)
                tag_ids[6 + l_idx + 2 * stack_idx] = self.tag2id.get(
                    tag, self.unk_tag_id)
                deprel_ids[l_idx + 2 * stack_idx] = self.deprel2id.get(
                    deprel, self.unk_deprel_id)
                if not l_idx:  # leftmost-leftmost
                    for ll_dep in pp.get_nleftmost(l_dep, n=1):
                        word, tag = pp.sentence[ll_dep]
                        deprel = next(
                            arc[2] for arc in pp.arcs if arc[1] == ll_dep)
                        word_ids[14 + stack_idx] = self.word2id.get(
                            word, self.unk_word_id)
                        tag_ids[14 + stack_idx] = self.tag2id.get(
                            tag, self.unk_tag_id)
                        deprel_ids[8 + stack_idx] = self.deprel2id.get(
                            deprel, self.unk_deprel_id)
            # first 2 rightmost
            for r_idx, r_dep in enumerate(
                    pp.get_nrightmost(sentence_idx, n=2)):
                word, tag = pp.sentence[r_dep]
                deprel = next(arc[2] for arc in pp.arcs if arc[1] == r_dep)
                word_ids[10 + r_idx + 2 * stack_idx] = self.word2id.get(
                    word, self.unk_word_id)
                tag_ids[10 + r_idx + 2 * stack_idx] = self.tag2id.get(
                    tag, self.unk_tag_id)
                deprel_ids[4 + r_idx + 2 * stack_idx] = self.deprel2id.get(
                    deprel, self.unk_deprel_id)
                if not r_idx:  # rightmost-rightmost
                    for rr_dep in pp.get_nrightmost(r_dep, n=1):
                        word, tag = pp.sentence[rr_dep]
                        deprel = next(
                            arc[2] for arc in pp.arcs if arc[1] == rr_dep)
                        word_ids[16 + stack_idx] = self.word2id.get(
                            word, self.unk_word_id)
                        tag_ids[16 + stack_idx] = self.tag2id.get(
                            tag, self.unk_tag_id)
                        deprel_ids[10 + stack_idx] = self.deprel2id.get(
                            deprel, self.unk_deprel_id)
        for buf_idx, sentence_idx in enumerate(
                range(pp.next, min(pp.next + 3, len(pp.sentence)))):
            word, tag = pp.sentence[sentence_idx]
            word_ids[buf_idx + 3] = self.word2id.get(word, self.unk_word_id)
            tag_ids[buf_idx + 3] = self.tag2id.get(tag, self.unk_tag_id)
        return word_ids, tag_ids, deprel_ids

    def pps2feats(self, pps):
        """Partial parses to feature vector triples"""
        feats = (self.pp2feat(pp) for pp in pps)
        return zip(*feats)

    def graphs2feats_and_tds(self, graphs):
        """From graphs, construct feature vector triples and trans,dep vecs

        Intended for training. This method takes in gold-standard
        dependency trees and yields pairs of (feat_vec, td_vec),
        where feat_vec are feature vectors as described in pp2feat, and
        td_vec is a (2 * len(self.id2deprel) + 1)-long
        float32 vector that encodes the transition operation as follows:

         - index 0 encodes the shift op
         - indices 1 to len(self.id2deprel) + 1 incl. encode the
           left-arc with dependency relations, excluding the "null"
           deprel
         - incides len(self.id2deprel) + 1 to
           2 * len(self.id2deprel) + 1  encode the right-arc with
           dependency relations, excluding the "null" deprel
         - len(self.id2deprel) + 1 to 2 * len(self.id2deprel) + 1  encode the
           right-arc with dependency relations, excluding the "null" deprel

        It uses PartialParses' get_oracle method to determine the
        arc standard form. If a graph is non-projective, this generator
        will skip the instance.
        """
        for graph in graphs:
            pp = PartialParse(get_sentence(graph))
            td_vecs = []
            feat_tups = []
            try:
                while not pp.complete:
                    transition_id, deprel = pp.get_oracle(graph)
                    td_vec = np.zeros(
                        2 * len(self.id2deprel) + 1, dtype=np.float32)
                    if transition_id == pp.shift_id:
                        td_vec[0] = 1.
                    else:
                        deprel_id = self.deprel2id.get(deprel,
                                                       self.unk_deprel_id)
                        if transition_id == pp.left_arc_id:
                            td_vec[1 + deprel_id] = 1.
                        else:
                            td_vec[1 + len(self.id2deprel) + deprel_id] = 1.
                    td_vecs.append(td_vec)
                    feat_tups.append(self.pp2feat(pp))
                    pp.parse_step(transition_id, deprel)
            except (ValueError, IndexError):
                # no parses. If PartialParse is working, this occurs
                # when the graph is non-projective. Skip the instance
                continue
            for feat_tup, td_vec in zip(feat_tups, td_vecs):
                yield feat_tup, td_vec

    def remove_deprels(self, feats_and_tds):
        """Removes deprels from feat vec and trans/deprel vec

        Useful for converting LAS task to UAS
        """
        for feat_vec, td_vec in feats_and_tds:
            if td_vec[0]:
                td_vec = np.array((1, 0, 0), dtype=np.float32)
            elif np.sum(td_vec[1:len(self.deprel2id)]):
                td_vec = np.array((0, 1, 0), dtype=np.float32)
            else:
                td_vec = np.array((0, 0, 1), dtype=np.float32)
            yield feat_vec[:2], td_vec

    def feats_and_tds2minibatches(self, feats_and_tds, max_batch_size,
                                  has_deprels=True):
        """Convert (feats,...),(trans, deprel) pairs to minibatches

        Args:
            has_deprels : Whether features and labels have dependency
                labels (for LAS)
        """
        batch_size = 0
        cur_batch = None
        for feat_vecs, td_vec in feats_and_tds:
            if not batch_size:
                if has_deprels:
                    cur_batch = (
                        (np.empty((max_batch_size, 18), dtype=np.int64),
                         np.empty((max_batch_size, 18), dtype=np.int64),
                         np.empty((max_batch_size, 12), dtype=np.int64),
                         ),
                        np.empty(
                            (max_batch_size, 2 * len(self.id2deprel) + 1),
                            dtype=np.int64),
                        )
                else:
                    cur_batch = (
                        (np.empty((max_batch_size, 18), dtype=np.int64),
                         np.empty((max_batch_size, 18), dtype=np.int64),
                         ),
                        np.empty((max_batch_size, 3), dtype=np.int64),
                        )
            for feat_vec_idx in range(len(feat_vecs)):
                cur_batch[0][feat_vec_idx][batch_size] = \
                    feat_vecs[feat_vec_idx]
            cur_batch[1][batch_size] = td_vec
            batch_size += 1
            if batch_size == max_batch_size:
                yield cur_batch
                batch_size = 0
        if batch_size:
            yield (tuple(feat[:batch_size] for feat in cur_batch[0]),
                   cur_batch[1][:batch_size])

    def td_vec2trans_deprel(self, td_vec, shift_id=None,
                            left_arc_id=None,
                            right_arc_id=None,
                            has_deprel=True):
        """Convert a trans/deprel vector into a trans,deprel pair

        The maximum value index is chosen as the transition to take

        Args:
            has_deprel : whether td_vec contains the deprel or is
                simply a one-hot of transitions

        Returns:
            (transition_id, deprel) where deprel is always None if
            has_deprel is false
        """

        if shift_id is None:
            shift_id = PartialParse.shift_id
        if left_arc_id is None:
            left_arc_id = PartialParse.left_arc_id
        if right_arc_id is None:
            right_arc_id = PartialParse.right_arc_id

        max_idx = np.argmax(td_vec)
        if not has_deprel:
            return (shift_id, left_arc_id, right_arc_id)[max_idx], None
        elif not max_idx:
            return shift_id, None
        elif max_idx <= len(self.id2deprel):
            return left_arc_id, self.id2deprel[max_idx - 1]
        else:
            return (right_arc_id,
                    self.id2deprel[max_idx - len(self.id2deprel) - 1])


class TrainingIterable(object):
    """Produces iterators over training data

    Args:
        graphs: the underlying CorpusView of DependencyGraphs
        transducer : an appropriately initialized Transducer
        seed : int
            The seed used to randomize the order per epoch
        max_batch_size : int
            The size of the batch to yield, except at edges. None yields
            one at a time
        las : bool
            Whether to set up input/labels for LAS task or UAS
        transition_cache : int or None
            How many transitions to cache before shuffling. If set,
            the graphs will be shuffled ahead of time, but the
            transitions within a graph will only be dispersed by
            approximately transition_cache / 2 samples. This option
            avoids storing all the training data in memory. If None,
            the entire data set will have to be stored in memory
    """

    def __init__(self, graphs, transducer, seed=1234, max_batch_size=2048,
                 las=True, transition_cache=None, n_ex=0):
        self.graphs = graphs
        self.graphs_len = len(graphs)
        self.transducer = transducer
        self.rng = np.random.RandomState(seed)
        self.max_batch_size = max_batch_size
        if self.graphs_len > 2 ** 16 - 1:
            self.idx_map_dtype = np.uint32
        else:
            self.idx_map_dtype = np.uint16
        self.las = las
        self.transition_cache = transition_cache
        self._len = n_ex
        if self.transition_cache is not None:
            self.all_data = None
            self._len = self._len or sum(
                1 for _ in self.transducer.graphs2feats_and_tds(self.graphs))
        else:
            self._construct_all_data()
            self._len = len(self.all_data[0])

    def _construct_all_data(self):
        """Pull all data for when transition_cache is None"""
        data_iter = self.transducer.graphs2feats_and_tds(self.graphs)
        if self.las:
            feat_vecs_lists = ([], [], [])
        else:
            feat_vecs_lists = ([], [])
            data_iter = self.transducer.remove_deprels(data_iter)
        td_vecs_list = []
        print('Pre-processing training sentences...')
        for feat_vecs, td_vec in tqdm(data_iter, total=self._len, leave=False,
                                      unit='ex', unit_scale=True):
            for feat_vec, feat_vecs_list in zip(feat_vecs, feat_vecs_lists):
                feat_vecs_list.append(feat_vec)
            td_vecs_list.append(td_vec)
        self.all_data = (
            feat_vecs_lists[0], feat_vecs_lists[1], feat_vecs_lists[2],
            td_vecs_list
            )

    def _shuffled_graphs(self):
        """Get graphs, shuffled"""
        idx_map = np.arange(self.graphs_len, dtype=self.idx_map_dtype)
        self.rng.shuffle(idx_map)
        for idx_idx in range(self.graphs_len):
            yield self.graphs[idx_map[idx_idx]]

    def _shuffled_transitions(self, feats_and_tds):
        """Shuffle transitions to the length of the transition_cache"""
        while True:
            cache = list(islice(feats_and_tds, self.transition_cache))
            if not cache:
                break
            self.rng.shuffle(cache)
            for elem in cache:
                yield elem

    def _shuffled_all_data(self):
        """Shuffle all data (cached) and return one by one"""
        idx_map = np.arange(self._len, dtype=np.uint32)
        self.rng.shuffle(idx_map)
        for idx_idx in range(self._len):
            idx = idx_map[idx_idx]
            yield (tuple(x[idx] for x in self.all_data[:3]),
                   self.all_data[3][idx])

    def get_iterator(self, shuffled=True):
        """Get data iterator over an epoch

        Args:
            shuffled : bool
                Whether to shuffle the data
        """
        if self.transition_cache is None:
            if shuffled:
                ret_iter = self._shuffled_all_data()
            else:
                ret_iter = ((tup[:3], tup[3]) for tup in zip(*self.all_data))
        else:
            if shuffled:
                ret_iter = self._shuffled_graphs()
            else:
                ret_iter = self.graphs
            ret_iter = self.transducer.graphs2feats_and_tds(ret_iter)
            if shuffled and self.transition_cache != 0:
                ret_iter = self._shuffled_transitions(ret_iter)
            if not self.las:
                ret_iter = self.transducer.remove_deprels(ret_iter)
        ret_iter = self.transducer.feats_and_tds2minibatches(
            ret_iter, self.max_batch_size, has_deprels=self.las)
        return ret_iter

    def __len__(self):
        return self._len

    def __iter__(self):
        return self.get_iterator()


def score_arcs(actuals, expecteds, las=True):
    """Return UAS or LAS score of arcs"""
    accum = accum_l = 0.
    tokens = 0
    for actual, expected in zip(actuals, expecteds):
        sent_len = len(expected)
        tokens += sent_len
        ex = {}
        for (ex_h, ex_d, ex_dep) in expected:
            ex[ex_d] = (ex_h, ex_dep)
        for (ac_h, ac_d, ac_dep) in actual:
            if las:
                accum_l += (ex.get(ac_d) == (ac_h, ac_dep))
            accum += int(ex.get(ac_d) is not None and ex.get(ac_d)[0] == ac_h)
    return (accum_l / max(tokens, 1)) if las else None, accum / max(tokens, 1)


def load_metadata(md_path):
    if md_path.is_file():
        with md_path.open('rb') as md_in:
            md = load(md_in)
        return md['g_len'], md['t_len']
    else:
        return 0, 0


def save_metadata(md_path, g_len, t_len):
    if not md_path.is_file():
        md_dict = {'g_len': g_len, 't_len': t_len}
        with md_path.open('wb') as md_out:
            dump(md_dict, md_out)

def load_and_preprocess_data(
        dir_path=Path('/u/csc485h/fall/pub/a1'),
        word_embedding_path=Path('word2vec.pkl.gz'),
        las=True, max_batch_size=2048,
        transition_cache=None, seed=1234):
    """Get train/test data

    See TrainingIterable for description of args

    Returns:
         a tuple of
         - a Transducer object
         - a word embedding matrix
         - a training data iterable
         - an iterable over dev sentences
         - an iterable over dev dependencies (arcs)
         - an iterable over test sentences
         - an iterable over test dependencies (arcs)
    """

    data_set = LazyCorpusLoader(
        'UD_English-EWT', UniversalDependencyCorpusReader, r'.*\.conllu', nltk_data_subdir=dir_path/'corpora')

    print('Loading word embeddings...')
    stdout.flush()
    if word_embedding_path.name.endswith('.gz'):
        with gz_open(word_embedding_path, 'rb') as file_obj:
            word_list, word_embeddings = load(file_obj)
    else:
        with open(word_embedding_path, 'rb') as file_obj:
            word_list, word_embeddings = load(file_obj)
    # add null embedding (we'll initialize later)
    word_embeddings = np.append(
        word_embeddings,
        np.empty((1, word_embeddings.shape[1]), dtype=np.float32),
        axis=0
        )
    print('There are {} word embeddings.'.format(word_embeddings.shape[0]))

    print('Getting training sentences...')
    stdout.flush()
    training_graphs = data_set.parsed_sents('en_ewt-ud-train.conllu')
    metadata_path = Path(data_set.root.path) / 'meta.pkl'
    trn_sent, trn_ex = load_metadata(metadata_path)
    tag_set, deprel_set = set(), set()
    with tqdm(total=trn_sent or None, leave=False, unit='sent') as progbar:
        for graph in training_graphs:
            for node in graph.nodes.values():
                if node['address']:  # not root
                    tag_set.add(node['ctag'])
                    deprel_set.add(node['rel'])
            progbar.update()
    tag_list = sorted(tag_set)
    if las:
        deprel_list = sorted(deprel_set)
        status = 'There are {} tags and {} deprel labels'
        status = status.format(len(tag_list), len(deprel_list))
    else:
        deprel_list = []
        status = 'There are {} tags'
        status = status.format(len(tag_list))
    transducer = Transducer(word_list, tag_list, deprel_list)
    training_data = TrainingIterable(training_graphs, transducer, seed=seed,
                                     max_batch_size=max_batch_size, las=las,
                                     transition_cache=transition_cache,
                                     n_ex=trn_ex)
    # use training's rng to initialize null embedding
    word_embeddings[-1] = training_data.rng.uniform(-.01, .01, 50)
    print(status,
          'from {} training sentences.'.format(training_data.graphs_len))
    save_metadata(metadata_path, len(training_graphs), len(training_data))

    print('Getting dev sentences...')
    stdout.flush()
    dev_sentences = data_set.tagged_sents('en_ewt-ud-dev.conllu')
    dev_arcs = tuple(list(transducer.graph2arc(graph, include_deprel=las))
                     for graph in tqdm(data_set.parsed_sents('en_ewt-ud-dev.conllu'),
                                       leave=False, unit='sent')
                     )
    print('There are {} dev sentences.'.format(len(dev_arcs)))

    print('Getting test sentences...')
    stdout.flush()
    test_sentences = data_set.tagged_sents('en_ewt-ud-test.conllu')
    test_arcs = tuple(list(transducer.graph2arc(graph, include_deprel=las))
                     for graph in tqdm(data_set.parsed_sents('en_ewt-ud-test.conllu'),
                                        leave=False, unit='sent')
                      )
    print('There are {} test sentences.'.format(len(test_arcs)))
    return (transducer, word_embeddings, training_data, dev_sentences,
            dev_arcs, test_sentences, test_arcs)


# Q2 Data

class SentIncl(IntFlag):
    NONPROJ = auto()
    PROJ = auto()
    BOTH = NONPROJ | PROJ

class UDData(Dataset):
    split_sent = re.compile('\n\n')

    def __init__(self, filepath: Path, deprels: T.Mapping[str, int], *,
                 include: SentIncl = SentIncl.BOTH, fraction: float = 1.):

        if not include:
            raise ValueError(f'must include some sentences!')
        if filepath.suffix == '.bz2':
            from bz2 import open as file_open
        else:
            file_open = open
        with file_open(filepath, 'rt') as data_in:
            data = data_in.read()
        sentences = [sent for sent in self.split_sent.split(data) if sent]
        if 0 < fraction < 1:
            sentences = sentences[:int(len(sentences) * fraction)]
        self.deprels_s2i = deprels

        proc = min(cpu_count(), 2)
        with Pool(proc) as pool:
            sentences = pool.map(self.parse_etc, sentences)
        sentences = [s for s in sentences if (s[-1] + 1) & include]
        data = [list(t) for t in zip(*sentences)]
        self.forms, self.heads, self.deprels, self.projective = data

    def parse_etc(self, s: str) -> T.Tuple[T.List[str, ...], T.Tuple[int, ...],
                                           T.List[int, ...], bool]:
        sen = parse_token_and_metadata(s).filter(id=lambda x: type(x) is int)
        forms, heads, deprels = zip(*[(t['form'], t['head'],
                                       self.deprels_s2i[t['deprel']])
                                      for t in sen])
        return forms, heads, deprels, is_projective(heads)

    def __getitem__(self, item: int) \
            -> T.Tuple[T.Tuple[str, ...], T.Tuple[int, ...],
                       T.List[int, ...], bool]:
        return (self.forms[item], self.heads[item], self.deprels[item],
                self.projective[item])

    def __len__(self) -> int:
        return len(self.forms)

    @classmethod
    def read(cls, data_dir, language: str, treebank: str, *, fraction: float = 1.) \
            -> T.Tuple[T.Optional[UDData], T.Optional[UDData],
                       T.Optional[UDData]]:
        root = data_dir / f'UD_{language}-{treebank}'
        deprel_s2i = {'root': 0}  # keep root deprel as 0 for simplicity
        for dep in ET.parse(root / 'stats.xml').getroot().iterfind('.//dep'):
            if (deprel := dep.attrib['name']) not in deprel_s2i:
                deprel_s2i[deprel] = len(deprel_s2i)
        datasets = []
        for split in ['train', 'dev', 'test']:
            if files := list(root.glob(f'*{split}.conll*')):
                assert len(files) == 1
                datasets.append(cls(files[0], deprel_s2i, fraction=fraction))
            else:
                datasets.append(None)

        return tuple(datasets)
