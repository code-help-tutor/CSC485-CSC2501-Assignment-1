WeChat: cstutorcs
QQ: 749389476
Email: tutorcs@163.com

"""Statistical modelling/parsing classes"""

import sys
from itertools import islice
from pathlib import Path
from sys import stdout

import numpy as np
import torch
import torch.nn as nn
from torch.nn.functional import cross_entropy
import einops
from tqdm import tqdm

from data import score_arcs
from q1_parse import minibatch_parse

class ParserModel(nn.Module):
    """
    Implements a feedforward neural network with an embedding layer and single
    hidden layer. This network will predict which transition should be applied
    to a given partial parse state.
    """
    def create_embeddings(self, word_embeddings: torch.Tensor) -> None:
        """Create embeddings that map word, tag, and deprels to vectors

        Args:
            word_embeddings:
                torch.Tensor of shape (n_word_ids, embed_size) representing
                matrix of pre-trained word embeddings

        Embedding layers convert sparse ID representations to dense vector
        representations.
         - Create 3 embedding layers using nn.Embedding, one for each of
           the input types:
           - The word embedding layer must be initialized with the value of the
             argument word_embeddings, so you will want to create it using
             nn.Embedding.from_pretrained(...). Make sure not to freeze the
             embeddings!
           - You don't need to do anything special for initializing the other
             two embedding layers, so use nn.Embedding(...) for them.
         - The relevant values for the number of embeddings for each type can
           be found in {n_word_ids, n_tag_ids, n_deprel_ids}.
         - Assign the layers to self as attributes:
               self.word_embed
               self.tag_embed
               self.deprel_embed
           (Don't use different variable names!)
        """
        # *** ENTER YOUR CODE BELOW *** #
        

    def create_net_layers(self) -> None:
        """Create layer weights and biases for this neural network

        Our neural network computes predictions from the embedded input
        using a single hidden layer as well as an output layer. This method
        creates the hidden and output layers, including their weights and
        biases (but PyTorch will manage the weights and biases; you will not
        need to access them yourself). Note that the layers will only compute
        the result of the multiplication and addition (i.e., no activation
        function is applied, so the hidden layer will not apply the ReLu
        function).

         - Create the two layers mentioned above using nn.Linear. You will need
           to fill in the correct sizes for the nn.Linear(...) calls. Keep in mind
           the layer sizes:
               input layer (x): N * embed_size
               hidden layer (h): hidden_size
               output layer (pred): n_classes
           where N = n_word_features + n_tag_features + n_deprel_features
         - Assign the two layers to self as attributes:
               self.hidden_layer
               self.output_layer
           (Don't use different variable names!)

        nn.Linear will take care of randomly initializing the weight and bias
        tensors automatically, so that's all that is to be done here.
        """
        # *** ENTER YOUR CODE BELOW *** #
        

    def reshape_embedded(self, input_batch: torch.Tensor) -> torch.Tensor:
        """Reshape an embedded input to combine the various embedded features

        Remember that we use various features based on the parser's state for
        our classifier, such as word on the top of the stack, next word in the
        buffer, etc. Each feature (such as a word) has its own embedding. But
        we will not want to keep the features separate for the classifier, so
        we must merge them all together. This method takes a tensor with
        separated embeddings for each feature and reshapes it accordingly.

        Args:
            input_batch:
                torch.Tensor of dtype float and shape (B, N, embed_size)
                where B is the batch_size and N is one of {n_word_features,
                n_tag_features, n_deprel_features}.
        Returns:
            reshaped_batch:
                torch.Tensor of dtype float and shape (B, N * embed_size).

         - Reshape the embedded batch tensor into the specified shape using
           torch.reshape. You may find the value of -1 handy for one of the
           shape dimensions; see the docs for torch.reshape for what it does.
           You may alternatively use the input_batch.view(...) or
           input_batch.reshape(...) methods if you prefer.
        """
        # *** ENTER YOUR CODE BELOW *** #
        
        return reshaped_batch

    def concat_embeddings(self, word_id_batch: torch.Tensor,
                              tag_id_batch: torch.Tensor,
                              deprel_id_batch: torch.Tensor) -> torch.Tensor:
        """Get, reshape, and concatenate word, tag, and deprel embeddings

        Recall that in our neural network, we concatenate the word, tag, and
        deprel embeddings to use as input for our hidden layer. This method
        retrieves all word, tag, and deprel embeddings and concatenates them
        together.

        Args:
            word_id_batch:
                torch.Tensor of dtype int64 and shape (B, n_word_features)
            tag_id_batch:
                torch.Tensor of dtype int64 and shape (B, n_tag_features)
            deprel_id_batch:
                torch.Tensor of dtype int64 and shape (B, n_deprel_features)
            where B is the batch size
        Returns:
            reshaped:
                torch.Tensor of dtype float and shape (B, N * embed_size) where
                N = n_word_features + n_tag_features + n_deprel_features

         - Look up the embeddings for the IDs represented by the word_id_batch,
           tag_id_batch, and deprel_id_batch tensors using the embedding layers
           you defined in self.create_embeddings. (You do not need to call that
           method from this one; that is done automatically for you elsewhere.)
         - Use the self.reshape_embedded method you implemented on each of the
           resulting embedded batch tensors from the previous step.
         - Concatenate the reshaped embedded inputs together using torch.cat to
           get the necessary shape specified above and return the result.
        """
        # *** ENTER YOUR CODE BELOW *** #
        return reshaped

    def forward(self,
                word_id_batch: np.array,
                tag_id_batch: np.array,
                deprel_id_batch: np.array) -> torch.Tensor:
        """Compute the forward pass of the single-layer neural network

        In our single-hidden-layer neural network, our predictions are computed
        as follows from the concatenated embedded input x:
          1. x is passed through the linear hidden layer to produce h.
          2. Dropout is applied to h to produce h_drop.
          3. h_drop is passed through the output layer to produce pred.
        This method computes pred from the x with the help of the setup done by
        the other methods in this class. Note that, compared to the assignment
        handout, we've added dropout to the hidden layer and we will not be
        applying the softmax activation at all in this model code. See the
        cross_entropy_loss method if you are curious as to why.

        Args:
            word_id_batch:
                np.array of dtype int64 and shape (B, n_word_features)
            tag_id_batch:
                np.array of dtype int64 and shape (B, n_tag_features)
            deprel_id_batch:
                np.array of dtype int64 and shape (B, n_deprel_features)
        Returns:
            pred: torch.Tensor of shape (B, n_classes)

        - Use self.hidden_layer that you defined in self.create_net_layers to
          compute the pre-activation hidden layer values.
        - Use the torch.relu function to activate the result of
          the previous step and then use the torch.dropout
          function to apply dropout with the appropriate dropout rate. You will use
          these function calls: torch.relu(...) and torch.dropout(...).
          - Remember that dropout behaves differently when training vs. when
          evaluating. The torch.dropout function reflects this via its arguments.
          You can use self.training to indicate whether or not the model is
          currently being trained.
        - Finally, use self.output_layer to compute the model outputs from the
          result of the previous step.
        """
        x = self.concat_embeddings(torch.tensor(np.array(word_id_batch)),
                                       torch.tensor(np.array(tag_id_batch)),
                                       torch.tensor(np.array(deprel_id_batch)))
        # *** ENTER YOUR CODE BELOW *** #
        
        return pred

    def cross_entropy_loss(self, pred_batch: torch.Tensor,
                 class_batch: torch.Tensor) -> torch.Tensor:
        """Calculate the value of the loss function

        In this case we are using cross entropy loss. The loss will be averaged
        over all examples in the current minibatch. This file already imports
        the function cross_entropy for you (line 14), so you can directly use
        `cross_entropy` to compute the loss. Note that we are not applying softmax
        to pred_batch, since cross_entropy handles that in a more efficient way.
        Excluding the softmax in predictions won't change the expected transition.
        (Convince yourself of this.)

        Args:
            pred_batch:
                A torch.Tensor of shape (batch_size, n_classes) and dtype float
                containing the logits of the neural network, i.e., the output
                predictions of the neural network without the softmax
                activation.
            class_batch:
                A torch.Tensor of shape (batch_size,) and dtype int64
                containing the ground truth class labels.
        Returns:
            loss: A 0d tensor (scalar) of dtype float
        """
        # *** ENTER YOUR CODE BELOW *** #
        
        return loss

    def add_optimizer(self):
        """Sets up the optimizer.

        Creates an instance of the Adam optimizer and sets it as an attribute
        for this class.
        """
        self.optimizer = torch.optim.Adam(self.parameters(), self.config.lr)

    def _fit_batch(self, word_id_batch, tag_id_batch, deprel_id_batch,
                   class_batch):
        self.optimizer.zero_grad()
        pred_batch = self(word_id_batch, tag_id_batch, deprel_id_batch)
        loss = self.cross_entropy_loss(pred_batch, torch.tensor(class_batch).argmax(-1))
        loss.backward()

        self.optimizer.step()

        return loss

    def fit_epoch(self, train_data, epoch, trn_progbar, batch_size=None):
        """Fit on training data for an epoch"""
        self.train()
        desc = 'Epoch %d/%d' % (epoch + 1, self.config.n_epochs)
        total = len(train_data) * batch_size if batch_size else len(train_data)
        bar_fmt = '{l_bar}{bar}| [{elapsed}<{remaining}{postfix}]'
        with tqdm(desc=desc, total=total, leave=False, miniters=1, unit='ex',
                  unit_scale=True, bar_format=bar_fmt, position=1) as progbar:
            trn_loss = 0
            trn_done = 0
            for ((word_id_batch, tag_id_batch, deprel_id_batch),
                 class_batch) in train_data:

                loss = self._fit_batch(word_id_batch, tag_id_batch,
                                       deprel_id_batch, class_batch)
                trn_loss += loss.item() * word_id_batch.shape[0]
                trn_done += word_id_batch.shape[0]
                progbar.set_postfix({'loss': '%.3g' % (trn_loss / trn_done)})
                progbar.update(word_id_batch.shape[0])
                trn_progbar.update(word_id_batch.shape[0] / total)
        return trn_loss / trn_done

    def predict(self, partial_parses):
        """Use this model to predict the next transitions/deprels of pps"""
        self.eval()
        feats = self.transducer.pps2feats(partial_parses)
        td_vecs = self(*feats).cpu().detach().numpy()
        preds = [
            self.transducer.td_vec2trans_deprel(td_vec) for td_vec in td_vecs]
        return preds

    def evaluate(self, sentences, ex_arcs):
        """LAS on either training or test sets"""
        act_arcs = minibatch_parse(sentences, self, self.config.batch_size)
        ex_arcs = tuple([(a[0], a[1],
                          self.transducer.id2deprel[a[2]]) for a in pp]
                        for pp in ex_arcs)
        stdout.flush()
        return score_arcs(act_arcs, ex_arcs)

    def __init__(self, transducer, config, word_embeddings):
        self.transducer = transducer
        self.config = config

        super().__init__()

        self.create_embeddings(torch.from_numpy(word_embeddings))
        self.create_net_layers()

        self.add_optimizer()