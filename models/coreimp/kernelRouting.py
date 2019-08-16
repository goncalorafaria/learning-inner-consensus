from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf

from ..core.routing import SimplifiedRoutingProcedure
from ..core.variables import weight_variable, bias_variable


class KernelRouting(SimplifiedRoutingProcedure):

    def __init__(
            self,
            kernel,
            metric,
            iterations,
            activate = True,
            name="",
            verbose=False):
        self._kernel = kernel
        self._agreement = None

        super(KernelRouting, self).__init__(
            name="KernelRouting" + name,
            metric=metric,
            design_iterations=iterations,
            initial_state=None,
            verbose=verbose)

    def _compatibility(self, s, r, votes, poses, probabilities, activations, it):
        ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }
        ## r :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

        alpha = weight_variable([],
                                name= "alpha",
                                verbose = self._verbose,
                                initializer=tf.compat.v1.keras.initializers.constant(value=1.0))

        poses_tiled = tf.tile(poses, [1, 1, 1, 1, self.atoms, 1, 1])

        self._agreement = self._kernel.take(poses_tiled, votes)

        r = activations * (1/tf.pow(alpha,2)) * self._agreement

        return r, s

    def _activation(self, s, c, votes, poses):
        ## poses :: { batch, output_atoms, new_w, new_h, 1 } + repdim
        ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
        ## c :: { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

        raw = tf.reduce_sum(tf.multiply(c, self._agreement), axis=-3, keepdims=True)

        if self._verbose:
            tf.compat.v1.summary.histogram(self.name + "dist_" + str(self._it), self._agreement)

        ## raw :: { batch, output_atoms, new_w, new_h, 1 } 

        theta1 = weight_variable([1], name="theta1", verbose=self._verbose)
        theta2 = bias_variable([1], name="theta2", verbose=self._verbose)

        if self._activate :
            activation = tf.sigmoid(theta1 * raw + theta2)
        else :
            activation = theta1 * raw + theta2
        ## activation :: { batch, output_atoms, new_w, new_h, 1 } 

        return activation

class KernelRoutingWithPrior(KernelRouting):

    def __init__(
            self,
            kernel,
            metric,
            iterations,
            activate = True,
            name="",
            verbose=False):
        super(KernelRoutingWithPrior, self).__init__(
            kernel=kernel,
            name="withPrior"+ name,
            metric=metric,
            iterations=iterations,
            activate=activate,
            verbose=verbose)

        self._normalization = tf.nn.softmax

    def _initial_coefficients(self, r, activations):
        return activations
