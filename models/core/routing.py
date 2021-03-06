from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc

import tensorflow as tf
import wandb

from ..core.metric import Metric
from ..core.variables import bias_variable

from opt_einsum import contract


class RoutingProcedure(object):
    """
        meta class for routing procedures.
    """
    __metaclass__ = abc.ABCMeta
    count = 0
    def __init__(
            self,
            name,
            metric,
            initial_state,
            design_iterations,
            normalization = tf.nn.softmax,
            epsilon=1e-6,
            bias=False,
            verbose=True):
        self._iterations = design_iterations
        self._design_iterations = design_iterations
        self._verbose = verbose
        self._normalization = normalization
        self._epsilon = epsilon
        self._initial_state = initial_state
        self.name = name
        self._bias= bias
        self.metric = metric
        self.atoms = 0
        self._it = 0
        self._activate = True

        self.inspection = {}

        RoutingProcedure.count += 1
        assert isinstance(metric, Metric), \
            " metric must be instance of Metric metaclass. "

    @abc.abstractmethod
    def _compatibility(self, s, r, votes, poses, probabilities,activations, it):
        ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        raise NotImplementedError('Not implemented')

    def compatibility(self, s, r, votes, poses, probabilities,activations, it):
        with tf.compat.v1.variable_scope('compatibility/', reuse=tf.compat.v1.AUTO_REUSE) as scope:
            return self._compatibility(s, r, votes, poses, probabilities, activations, it)

    @abc.abstractmethod
    def _activation(self, s, c, votes, poses):
        ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## c :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        raise NotImplementedError('Not implemented')

    def activation(self, s, c, votes, poses):
        with tf.compat.v1.variable_scope('activation', reuse=tf.compat.v1.AUTO_REUSE) as scope:
            return self._activation(s, c, votes, poses)

    def _initial_coefficients(self,activations):

        r = (1/32) * tf.ones(shape= activations.shape,
                    dtype=tf.float32,
                    name="compatibility_value")

        self._norm_coe = tf.reduce_sum(r, keepdims=True, axis=2)

        c = self._normalization(r, axis=4)

        return c

    def _renormalizedDotProd(self, c, votes):
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim

        vshape = votes.shape.as_list()

        raw_poses = contract("bowhitl,bowhiuv->bowhtuv",c,votes)

        self._norm_coe = tf.reduce_sum( c, axis=4, keepdims=True) + self._epsilon

        #print("#####renormalized")
        #print(self._it)
        #print(tf.reduce_sum(c))

        raw_poses_weight_normalized = raw_poses / self._norm_coe

        if self._bias:
            bvar = bias_variable(
                [1,vshape[1],vshape[2],vshape[3],1]+vshape[-2:],
                verbose=self._verbose,
                name="voting_bias"+str(RoutingProcedure.count)
            )
            raw_poses_weight_normalized = raw_poses_weight_normalized + bvar
        # raw_poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim

        #poses = tf.divide(raw_poses_weight_normalized, self._epsilon + self.metric.take(raw_poses_weight_normalized))
        # poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim

        return raw_poses_weight_normalized

    def bound_activations(self):
        self._activate = True

    def unbound_activations(self):
        self._activate = False

    def fit(self, votes, activations, iterations = 0):
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## activations { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

        self.atoms = votes.shape.as_list()[4]
        self.inspection["poses"] = votes

        with tf.compat.v1.variable_scope('RoutingProcedure' + self.name, reuse=tf.compat.v1.AUTO_REUSE):

            s = self._initial_state

            #activations = tf.reshape(activations, shape=activations.shape.as_list() + [1, 1])

            c = self._initial_coefficients(activations)
            ## c = self._normalization(r, axis=-3)

            ## r { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            ## c = self._normalization(r, axis=-3)
            ## c { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            poses = self._renormalizedDotProd(c, votes)

            ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
            self.inspection["poses0"] = poses

            probabilities, s = self.activation(s, c, votes, poses)
            ## probabilities :: { batch, output_atoms, new_w, new_h, 1 }


            if iterations == 0:
                self._iterations = self._design_iterations
            else :
                self._iterations = iterations

            for it in range(self._iterations):
                self._it = it

                self.inspection["s"+str(it)] = s

                self.inspection["c"+str(it) ] = c

                """
                import matplotlib.pyplot as plt

                plt.hist( c.numpy().reshape(-1), bins=400)
                plt.xlim((0, 1.05))
                plt.yscale("log")
                plt.show()
                """

                c, s = self.compatibility(s, c, votes, poses, probabilities, activations, it)
                ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

                #print("#####compatibility")
                #print(self._it)
                #print(tf.reduce_sum(c))

                poses = self._renormalizedDotProd(c, votes)
                ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim

                probabilities, s = self.activation(s, c, votes, poses)
                ## probabilities :: { batch, output_atoms, new_w, new_h, 1 }

            #probabilities = tf.squeeze(probabilities, axis=[-2,-1])
                self.inspection["poses"+str(it+1)] = poses

            if self._verbose:
                print("c:::###")
                print(c.shape)
                tf.compat.v1.summary.histogram("compatibilityact/" + self.name, c)
                best = tf.math.argmax(c, axis=4)
                tf.compat.v1.summary.histogram("bestC", best)
                self.inspection["cfinal"] = c
                self.inspection["cbest"] = best

            poses = contract("bowhlij->bwhoij",poses)
            probabilities = contract("bowhlij->bwhoij",probabilities)

            if self._verbose:
                tf.compat.v1.summary.histogram("RoutingProbabilities/" + self.name, probabilities)
                #print(probabilities.shape)

                #probabilities.reshape( (probabilities.shape[0], -1) )
                best = tf.math.argmax(
                    tf.reshape(probabilities,
                        (probabilities.shape[0], -1)
                        ),
                        axis=-1)
                tf.compat.v1.summary.histogram("bestProb",best)
                self.inspection["prob"] = probabilities
                self.inspection["prob_best"] = best

            return poses, probabilities


class SimplifiedRoutingProcedure(RoutingProcedure):
    """
        meta class for routing procedures.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(
            self,
            name,
            metric,
            initial_state,
            design_iterations,
            normalization = tf.nn.softmax,
            epsilon=1e-6,
            bias=False,
            verbose=False):

        super(SimplifiedRoutingProcedure, self).__init__(
            name=name,
            metric=metric,
            initial_state=initial_state,
            design_iterations=design_iterations,
            normalization = normalization,
            epsilon=epsilon,
            bias=bias,
            verbose=verbose)

    @abc.abstractmethod
    def _activation(self, s, c, votes, poses, activations):
        ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## c :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        raise NotImplementedError('Not implemented')

    def activation(self, s, c, votes, poses, activations):
        with tf.compat.v1.variable_scope('activation', reuse=tf.compat.v1.AUTO_REUSE) as scope:
            return self._activation(s, c, votes, poses, activations)

    def _renormalizedDotProd(self, c, votes):
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim

        vshape = votes.shape.as_list()

        #raw_poses = tf.reduce_sum(tf.multiply(c, votes), axis=4, keepdims=True)
        raw_poses = contract("bowhitl,bowhiuv->bowhtuv",c,votes)

        if self._bias:
            bvar = bias_variable(
                [1,vshape[1],vshape[2],vshape[3],1]+vshape[-2:],
                verbose=self._verbose,
                name="voting_bias"+str(RoutingProcedure.count)
            )
            raw_poses = raw_poses + bvar
        # raw_poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        # poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim

        return raw_poses

    def fit(self, votes, activations, iterations = 0):
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## activations { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        self.atoms = votes.shape.as_list()[4]
        self.inspection["poses"] = votes
        with tf.compat.v1.variable_scope('SimplifiedRoutingProcedure/' + self.name, reuse=tf.compat.v1.AUTO_REUSE):

            s = self._initial_state

            #activations = tf.reshape(activations, shape=[-1] + activations.shape.as_list()[1:] + [1, 1])

            c = self._initial_coefficients(activations)
            ## r { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            #c=r
            ## c { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            poses = self._renormalizedDotProd(c, votes)
            ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
            self.inspection["poses0"] = poses
            #probabilities = self.activation(s, c, votes, poses)
            ## probabilities :: { batch, output_atoms, new_w, new_h, 1 }

            if iterations == 0:
                self._iterations = self._design_iterations
            else :
                self._iterations = iterations

            for it in range(self._iterations):
                self._it = it


                self.inspection["c"+str(it) ] = c

                #if self._verbose:
                #        cshape = c.shape.as_list()
                #    c_hist = tf.reshape(c, [cshape[0], -1, cshape[4]])
                #    for i in range(c_hist.shape[1]):
                #        tf.compat.v1.summary.histogram(self.name + "c_" + str(self._it), c_hist[0, i, :])

                c, s = self.compatibility(s, c, votes, poses, None, activations, it)
                ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

                #c=r
                ## c :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

                poses = self._renormalizedDotProd(c, votes)
                ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
                self.inspection["poses"+str(it+1)] = poses

            if self._verbose:
                print("c:::###")
                print(c.shape)
                tf.compat.v1.summary.histogram("compatibilityact/" + self.name, c)
                best = tf.math.argmax(c, axis=4)
                tf.compat.v1.summary.histogram("bestC", best)
                self.inspection["cfinal"] = c
                self.inspection["cbest"] = best

            #if self._verbose:
            #    cshape = c.shape.as_list()
        #        c_hist = tf.reshape(c, [ cshape[0], -1, cshape[4]])
            #    for i in range(c_hist.shape[1]):
            #        tf.compat.v1.summary.histogram(self.name+"c_"+str(self._iterations), c_hist[0,i,:])

            probabilities = self.activation(s, c, votes, poses, activations)
            ## probabilities :: { batch, output_atoms, new_w, new_h, 1 }

            #probabilities = tf.squeeze(probabilities, axis=[-2,-1])

            #poses = tf.transpose(poses, [0, 4, 2, 3, 1, 5, 6])  ## output atoms become depth
            #probabilities = tf.transpose(probabilities, [0, 4, 2, 3, 1, 5, 6])  ## output atoms become depth

            #poses = tf.squeeze(poses, axis=[1])  ## remove output atoms dim
            #probabilities = tf.squeeze(probabilities, axis=[1])  ## remove output atoms dim

            poses = contract("bowhlij->bwhoij",poses)
            probabilities = contract("bowhlij->bwhoij",probabilities)

            if self._verbose:
                tf.compat.v1.summary.histogram("RoutingProbabilities/" + self.name, probabilities)
                #print(probabilities.shape)

                #probabilities.reshape( (probabilities.shape[0], -1) )
                best = tf.math.argmax(
                    tf.reshape(probabilities,
                        (probabilities.shape[0], -1)
                        ),
                        axis=-1)
                tf.compat.v1.summary.histogram("bestProb",best)
                self.inspection["prob"] = probabilities
                self.inspection["prob_best"] = best

            return poses, probabilities


class HyperSimplifiedRoutingProcedure(RoutingProcedure):
    """
        meta class for routing procedures.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(
            self,
            name,
            metric,
            normalization = tf.nn.softmax,
            epsilon=1e-6,
            bias=False,
            verbose=False):


        super(HyperSimplifiedRoutingProcedure, self).__init__(
            name=name,
            metric=metric,
            initial_state=None,
            design_iterations=None,
            normalization = normalization,
            epsilon=epsilon,
            bias=bias,
            verbose=verbose)



    def fit(self, votes, activations, iterations = 0):
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## activations { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        self.atoms = votes.shape.as_list()[4]

        with tf.compat.v1.variable_scope('HyperSimplifiedRoutingProcedure/' + self.name, reuse=tf.compat.v1.AUTO_REUSE):

            r, s = self.compatibility(None, None, votes, None, None, activations, None)
            ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            c = self._normalization(r, axis=4)
            #c = r
            ## c :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            poses = self._renormalizedDotProd(c, votes)
            ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim

            probabilities = self.activation(s, c, votes, poses)
            ## probabilities :: { batch, output_atoms, new_w, new_h, 1 }

            probabilities = tf.squeeze(probabilities, axis=[-2, -1])

            poses = tf.transpose(poses, [0, 4, 2, 3, 1, 5, 6])  ## output atoms become depth
            probabilities = tf.transpose(probabilities, [0, 4, 2, 3, 1])  ## output atoms become depth

            poses = tf.squeeze(poses, axis=[1])  ## remove output atoms dim
            probabilities = tf.squeeze(probabilities, axis=[1])  ## remove output atoms dim

            #if self._verbose:
                #tf.compat.v1.summary.histogram("RoutingProbabilities/" + self.name, probabilities)

            return poses, probabilities
