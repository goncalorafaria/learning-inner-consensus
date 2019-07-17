from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf

from ..core.routing import RoutingProcedure
from ..core.transform import Transform


class CapsuleLayer(object):
    def __init__(
            self,
            routing,
            transform,
            ksizes,
            iterations = 0,
            name="",
            padding="VALID",
            strides=[1, 1, 1, 1]):
        self._routing = routing
        self._iterations = iterations
        self._ksizes = ksizes
        self._strides = strides
        self._transform = transform
        self._padding = padding
        self.name = name
        self._representation_dim = []

        assert isinstance(routing, RoutingProcedure), \
            " Must include an adequate routing procedure. "

        assert isinstance(transform, Transform), \
            " Must include an adequate transformation. "

        assert len(ksizes) == 4, \
            " ksizes must be length 4"

        assert len(strides) == 4, \
            " strides must be length 4"

        assert (padding is "VALID" or padding is "SAME"), \
            " padding must be VALID or SAME"

    def _receptivefield(self, input_tensor):
        ##  input_tensor == {  batch, w , h , depth } + repdim , {batch, w, h,  depth }

        poses, activations = input_tensor

        poses_shape = poses.shape.as_list()

        poses = tf.transpose(poses, [0, 4, 5, 1, 2, 3])
        ## poses = { batch } + repdim + { w , h , depth }

        condensed_poses = tf.reshape(poses,
                                     shape=[poses_shape[0], np.prod(self._representation_dim)] + poses_shape[1:4])
        ## condensed_poses = { batch, np.prod(repdim), w , h, depth }

        patched_poses = tf.extract_volume_patches(
            condensed_poses,
            ksizes=[self._ksizes[0], 1] + self._ksizes[1:],
            strides=[self._strides[0], 1] + self._strides[1:],
            padding=self._padding,
        )

        patched_activations = tf.compat.v1.extract_image_patches(
            activations,
            sizes=self._ksizes,
            strides=self._strides,
            padding=self._padding,
            rates=[1, 1, 1, 1]
        )
        ## patched_poses { batch, np.prod(repdim), new_w , new_h, depth * np.prod(ksizes) }
        ## patched_activations { batch, new_w , new_h, depth * np.prod(ksizes) }

        patched_poses = tf.transpose(patched_poses, [0, 2, 3, 4, 1])

        ## patched_poses { batch, new_w , new_h, depth * np.prod(ksizes), np.prod(repdim) }
        patched_poses_shape = patched_poses.shape.as_list()

        patched_poses = tf.reshape(patched_poses, patched_poses_shape[:4] + self._representation_dim)
        ## patched_poses { batch, new_w , new_h, depth * np.prod(ksizes)} + repdim }

        return patched_poses, patched_activations

    def inference(self, input_tensor):
        """ pre conditions """

        """ 
        type conditions 
        
            input_tensor == {  batch, w , h , depth } + repdim , {batch, w, h,  depth }
            
        """
        assert len(input_tensor) == 2, \
            "Input tensor must be a tuple of 2 elements."

        assert len(input_tensor[0].shape.as_list()) == 6, \
            " pose must be a rank 6 tensor."

        assert len(input_tensor[1].shape.as_list()) == 4, \
            " activations must be a rank 4 tensor."

        assert input_tensor[0].shape.as_list()[0] == input_tensor[1].shape.as_list()[0], \
            " batch dimention must match."

        assert input_tensor[0].shape.as_list()[1] == input_tensor[1].shape.as_list()[1], \
            " height dimention must match."

        assert input_tensor[0].shape.as_list()[2] == input_tensor[1].shape.as_list()[2], \
            " width dimention must match."

        assert input_tensor[0].shape.as_list()[3] == input_tensor[1].shape.as_list()[3], \
            " depth dimention must match."

        """Adds the inference graph ops."""

        self._representation_dim = input_tensor[0].shape.as_list()[4:]

        with tf.compat.v1.variable_scope('CapsuleLayer' + self.name, reuse=tf.compat.v1.AUTO_REUSE) as scope:
            poses, activations = self._receptivefield(input_tensor)
            """
                stacks the multiple possible receptive fields of capsules. 

            """
            ## poses { batch, new_w , new_h, depth * np.prod(ksizes)} + repdim
            ## activations { batch, new_w , new_h, depth * np.prod(ksizes) } 

            votes, activations = self._transform.translate(poses, activations)
            """
                transforms the poses to the output's capsule space.
            """
            ## votes :: { batch, output_atoms, new_w, new_h, depth * np.prod(ksizes) } + repdim
            ## activations { batch, output_atoms, new_w , new_h, depth * np.prod(ksizes) }

            higher_poses, higher_activations = self._routing.fit(
                votes,
                activations,
                iterations=self._iterations
            )
            """
                determines the pose and activation of the output capsules.
            """

            ## higher_poses :: { batch, new_w, new_h, output_atoms } + repdim
            ## higher_activations :: { batch, new_w, new_h,output_atoms }

            assert len(higher_poses.shape.as_list()) == 6, \
                " higher pose must be a rank 6 tensor."

            assert len(higher_activations.shape.as_list()) == 4, \
                " higher activations must be a rank 4 tensor."

            assert higher_poses.shape.as_list()[0] == higher_activations.shape.as_list()[0], \
                " batch dimention must match"

            assert input_tensor[0].shape.as_list()[0] == higher_poses.shape.as_list()[0], \
                " batch dimention must be perserved"

            return higher_poses, higher_activations


class FullyConnectedCapsuleLayer(CapsuleLayer):
    def __init__(
            self,
            routing,
            transform,
            name="",
    ):
        super(FullyConnectedCapsuleLayer, self).__init__(
            routing=routing,
            transform=transform,
            ksizes=[1, 1, 1, 1],
            name="FullyConnected/" + name)

    def inference(self, input_tensor):
        ## input_tensor == {batch, w, h, depth} + repdim, {batch, w, h, depth}

        poses, activations = input_tensor
        print(poses.shape)
        poses = tf.reshape(poses, [poses.shape[0], 1, 1, -1] + poses.shape.as_list()[4:])
        activations = tf.reshape(activations, [poses.shape[0], 1, 1, -1])

        print(poses.shape)
        return super(FullyConnectedCapsuleLayer, self).inference((poses, activations))


class CapsuleClassLayer(object):
    def __init__(
            self,
            normalized=False,
            name=""
    ):
        self._normalized = normalized
        self.name = name

    def inference(self, input_tensor):
        ## input_tensor == {batch, w, h, depth} + repdim, {batch, w, h, depth}

        with tf.compat.v1.variable_scope('toClassLayer/' + self.name,reuse=tf.compat.v1.AUTO_REUSE) as scope:
            poses, activations = input_tensor

            poses = tf.reshape(poses, [poses.shape[0], -1] + poses.shape.as_list()[4:])
            activations = tf.reshape(activations, [poses.shape[0], -1])

            if self._normalized:
                activations = tf.nn.softmax(activations, axis=-1)

            ## input_tensor == {batch, w*h* depth} + repdim, {batch, w * h * depth}
            return poses, activations


class PrimaryCapsuleLayer(object):
    def __init__(
            self,
            pose_dim,
            ksize=[1, 1],
            groups=10
    ):
        self._pose_dim = pose_dim
        self._ksize = ksize
        self._groups = groups

    def inference(self, input_tensor):
        ## input_tensor == {batch, w, h, depth}
        with tf.compat.v1.variable_scope('PrimaryCapsuleLayer/', reuse=tf.compat.v1.AUTO_REUSE):
            conv_pose = tf.keras.layers.Conv2D(
                filters=np.prod(self._pose_dim) * self._groups,
                kernel_size=self._ksize,
                activation='relu',
                use_bias=True,
                padding="SAME"
            )

            conv_activation = tf.keras.layers.Conv2D(
                filters=self._groups,
                kernel_size=self._ksize,
                activation='sigmoid',
                use_bias=True,
                padding="SAME"
            )

            raw_poses = conv_pose(input_tensor)

            activations = conv_activation(input_tensor)

            raw_poses_shape = raw_poses.shape.as_list()

            poses = tf.reshape(
                raw_poses,
                shape=raw_poses_shape[:3] + [self._groups] + self._pose_dim
            )

            ## pose == {batch, w, h, capsule_groups} + pose_dim
            ## activation == {batch, w, h, capsule_groups}

            return poses, activations
