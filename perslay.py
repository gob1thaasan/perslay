"""Module :mod:`perskay.archi` implement the persistence layer."""

# Authors: Mathieu Carriere <mathieu.carriere3@gmail.com>
# License: MIT
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf

# Post-processing operation with combination of batch normalization, dropout and relu
def _post_processing(vector, pro, dropout_value=.9):
    for c in pro:
        if c == "b":
            vector = tf.layers.batch_normalization(vector)
        if c == "d":
            vector = tf.nn.dropout(vector, dropout_value)
        if c == "r":
            vector = tf.nn.relu(vector)
    return vector

# DeepSet PersLay
def permutation_equivariant_layer(inp, dimension, perm_op, L_init, G_init, bias_init, L_const, G_const, bias_const):
    dimension_before, num_pts = inp.shape[2].value, inp.shape[1].value
    lbda = tf.get_variable("L", shape=[dimension_before, dimension], initializer=L_init)   if not L_const     else tf.get_variable("L", initializer=L_init)
    b    = tf.get_variable("b", shape=[1, 1, dimension], initializer=bias_init)            if not bias_const  else tf.get_variable("b", initializer=bias_init)
    A    = tf.reshape(tf.einsum("ijk,kl->ijl", inp, lbda), [-1, num_pts, dimension])
    if perm_op is not None:
        if perm_op == "max":
            beta = tf.tile(tf.expand_dims(tf.reduce_max(inp, axis=1), 1), [1, num_pts, 1])
        elif perm_op == "min":
            beta = tf.tile(tf.expand_dims(tf.reduce_min(inp, axis=1), 1), [1, num_pts, 1])
        elif perm_op == "sum":
            beta = tf.tile(tf.expand_dims(tf.reduce_sum(inp, axis=1), 1), [1, num_pts, 1])
        else:
            raise Exception("perm_op should be min, max or sum")
        gamma = tf.get_variable("G", shape=[dimension_before, dimension], initializer=G_init) if not G_const else tf.get_variable("G", initializer=G_init)
        B = tf.reshape(tf.einsum("ijk,kl->ijl", beta, gamma), [-1, num_pts, dimension])
        return A - B + b
    else:
        return A + b


# Gaussian PersLay
def gaussian_layer(inp, num_gaussians, mean_init, variance_init, mean_const, variance_const):
    dimension_before, num_pts = inp.shape[2].value, inp.shape[1].value
    mu = tf.get_variable("m", shape=[1, 1, dimension_before, num_gaussians], initializer=mean_init)      if not mean_const      else tf.get_variable("m", initializer=mean_init)
    sg = tf.get_variable("s", shape=[1, 1, dimension_before, num_gaussians], initializer=variance_init)  if not variance_const  else tf.get_variable("s", initializer=variance_init)
    bc_inp = tf.expand_dims(inp, -1)
    return tf.exp(tf.reduce_sum(-tf.multiply(tf.square(bc_inp - mu), tf.square(sg)), axis=2))


# Landscape PersLay
def landscape_layer(inp, num_samples, sample_init, sample_const):
    # num_pts = inp.shape[1].value
    sp = tf.get_variable("s", shape=[1, 1, num_samples], initializer=sample_init) if not sample_const else tf.get_variable("s", initializer=sample_init)
    return tf.maximum(inp[:, :, 1:2] - tf.abs(sp - inp[:, :, 0:1]), np.array([0]))


# Persistence Image PersLay
def image_layer(inp, image_size, image_bnds, variance_init, variance_const):
    dimension_before, num_pts = inp.shape[2].value, inp.shape[1].value
    coords = [tf.range(start=image_bnds[i][0], limit=image_bnds[i][1], delta=(image_bnds[i][1] - image_bnds[i][0]) / image_size[i]) for i in range(dimension_before)]
    M = tf.meshgrid(*coords)
    mu = tf.concat([tf.expand_dims(tens, 0) for tens in M], axis=0)
    sg = tf.get_variable("s", shape=[1, 1, 1] + [1 for _ in range(dimension_before)], initializer=variance_init) if not variance_const else tf.get_variable("s", initializer=variance_init)
    bc_inp = tf.reshape(inp, [-1, num_pts, dimension_before] + [1 for _ in range(dimension_before)])
    return tf.exp(tf.reduce_sum(-tf.multiply(tf.square(bc_inp - mu), tf.square(sg)), axis=2))

# PersLay channel for persistence diagrams
def perslay(output, name, diag, **kwargs):
            
    """
        output :   list on which perslay output will be appended
        name :     name of the operation for tensorflow
        diag :     big matrix of shape [N_diag, N_pts_per_diag, dimension_diag (coordinates of points) + 1 (mask--0 or 1)]
    """

    N, dimension_diag = diag.get_shape()[1], diag.get_shape()[2]
    tensor_mask = diag[:, :, dimension_diag - 1]
    tensor_diag = diag[:, :, :dimension_diag - 1]

    if kwargs["persistence_weight"] == "linear":
        with tf.variable_scope(name + "-linear_pweight"):
            C = tf.get_variable("C", shape=[1], initializer=kwargs["coeff_init"]) if not kwargs["coeff_const"] else tf.get_variable("C", initializer=kwargs["coeff_init"])
            weight = C * tf.abs(tensor_diag[:, :, 1:2])

    if kwargs["persistence_weight"] == "grid":
        with tf.variable_scope(name + "-grid_pweight"):
            W = tf.get_variable("W", shape=kwargs["grid_size"], initializer=kwargs["grid_init"]) if not kwargs["grid_const"] else tf.get_variable("W", initializer=kwargs["grid_init"])
            indices = []
            for dim in range(dimension_diag-1):
                [m, M] = kwargs["grid_bnds"][dim]
                coords = tf.slice(tensor_diag, [0, 0, dim], [-1, -1, 1])
                ids = kwargs["grid_size"][dim] * (coords - m)/(M - m)
                indices.append(tf.cast(ids, tf.int32))
            weight = tf.expand_dims(tf.gather_nd(params=W, indices=tf.concat(indices, axis=2)), -1)

    # First layer of channel: processing of the persistence diagrams by vectorization of diagram points
    if kwargs["layer"] == "pm":  # Channel with permutation equivariant layers
        for idx, (dim, pop) in enumerate(peq):
            with tf.variable_scope(name + "-perm_eq-" + str(idx)):
                tensor_diag = permutation_equivariant_layer(tensor_diag, dim, pop, kwargs["weight_init"], kwargs["weight_init"], kwargs["bias_init"], kwargs["weight_const"], kwargs["weight_const"], kwargs["bias_const"])
    elif kwargs["layer"] == "gs":  # Channel with gaussian layer
        with tf.variable_scope(name + "-gaussians"):
            tensor_diag = gaussian_layer(tensor_diag, kwargs["num_gaussians"], kwargs["mean_init"], kwargs["variance_init"], kwargs["mean_const"], kwargs["variance_const"])
    elif kwargs["layer"] == "ls":  # Channel with landscape layer
        with tf.variable_scope(name + "-samples"):
            tensor_diag = landscape_layer(tensor_diag, kwargs["num_samples"], kwargs["sample_init"], kwargs["sample_const"])
    elif kwargs["layer"] == "im":  # Channel with image layer
        with tf.variable_scope(name + "-bandwidth"):
            tensor_diag = image_layer(tensor_diag, kwargs["image_size"], kwargs["image_bnds"], kwargs["variance_init"], kwargs["variance_const"])

    output_dim = len(tensor_diag.shape) - 2

    vector = None  # to avoid warning

    if output_dim == 1:
        # Apply weight and mask
        if kwargs["persistence_weight"] is not None:
            tiled_weight = tf.tile(weight, [1, 1, tensor_diag.shape[2].value])
            tensor_diag = tf.multiply(tensor_diag, tiled_weight)
        tiled_mask = tf.tile(tf.expand_dims(tensor_mask, -1), [1, 1, tensor_diag.shape[2].value])
        masked_layer = tf.multiply(tensor_diag, tiled_mask)

        # Permutation invariant operation
        if kwargs["perm_op"] == "topk":  # k first values
            masked_layer_t = tf.transpose(masked_layer, perm=[0, 2, 1])
            values, indices = tf.nn.top_k(masked_layer_t, k=keep)
            vector = tf.reshape(values, [-1, keep * tensor_diag.shape[2].value])
        elif kwargs["perm_op"] == "sum":  # sum
            vector = tf.reduce_sum(masked_layer, axis=1)
        elif kwargs["perm_op"] == "max":  # maximum
            vector = tf.reduce_max(masked_layer, axis=1)
        elif kwargs["perm_op"] == "mean":  # minimum
            vector = tf.reduce_mean(masked_layer, axis=1)

        # Second layer of channel: fully-connected (None if fc_layers is set to [], default value)
        for idx, tup in enumerate(kwargs["fc_layers"]):
            # tup is a tuple whose element are
            # 1. dim of fully-connected,
            # 2. string for processing,
            # 3. (optional) dropout value
            with tf.variable_scope(name + "-fc-" + str(idx)):
                vector = tf.layers.dense(vector, tup[0])
            with tf.variable_scope(name + "-bn-" + str(idx)):
                if len(tup) == 2:
                    vector = _post_processing(vector, tup[1])
                else:
                    vector = _post_processing(vector, tup[1], tup[2])

    elif output_dim == 2:

        # Apply weight and mask
        if kwargs["persistence_weight"] is not None:
            weight = tf.expand_dims(weight, -1)
            tiled_weight = tf.tile(weight, [1, 1, tensor_diag.shape[2].value, tensor_diag.shape[3].value])
            tensor_diag = tf.multiply(tensor_diag, tiled_weight)
        tiled_mask = tf.tile(tf.reshape(tensor_mask, [-1, N, 1, 1]), [1, 1, tensor_diag.shape[2].value, tensor_diag.shape[3].value])
        masked_layer = tf.multiply(tensor_diag, tiled_mask)

        # Permutation invariant operation
        if kwargs["perm_op"] == "sum":  # sum
            vector = tf.reduce_sum(masked_layer, axis=1)
        elif kwargs["perm_op"] == "max":  # maximum
            vector = tf.reduce_max(masked_layer, axis=1)
        elif kwargs["perm_op"] == "mean":  # minimum
            vector = tf.reduce_mean(masked_layer, axis=1)

        # Second layer of channel: convolution
        vector = tf.expand_dims(vector, -1)
        for idx, tup in enumerate(kwargs["cv_layers"]):
            # tup is a tuple whose element are
            # 1. num of filters,
            # 2. kernel size,
            # 3. string for postprocessing,
            # 4. (optional) dropout value
            with tf.variable_scope(name + "-cv-" + str(idx)):
                vector = tf.layers.conv2d(vector, filters=tup[0], kernel_size=tup[1])
            with tf.variable_scope(name + "-bn-" + str(idx)):
                if len(tup) == 3:
                    vector = _post_processing(vector, tup[2])
                else:
                    vector = _post_processing(vector, tup[2], tup[3])
        vector = tf.layers.flatten(vector)

    output.append(vector)
    return vector