"""Implementation of Hessian-free optimization for recurrent networks.

Author: Daniel Rasmussen (drasmussen@princeton.edu)

Based on
Martens, J., & Sutskever, I. (2011). Learning recurrent neural networks with
hessian-free optimization. Proceedings of the 28th International Conference on
Machine Learning.
"""

import numpy as np

from hessianff import HessianFF


class HessianRNN(HessianFF):
    def __init__(self, shape, struc_damping=0.0, rec_layers=None,
                 W_rec_params={}, **kwargs):
        """Initialize the parameters of the network.

        :param struc_damping: controls scale of structural damping (relative
            to Tikhonov damping)
        :param rec_layers: by default, all layers except the first and last
            are recurrently connected. A list of booleans can be passed here
            to override that on a layer-by-layer basis.
        :param W_rec_params: weight initialization parameter dict for recurrent
            weights (passed to init_weights, see parameter descriptions there)

        See HessianFF for the rest of the parameters.
        """

        self.struc_damping = struc_damping

        if rec_layers is None:
            # assume all recurrent except first/last layer
            rec_layers = [False] + [True] * (len(shape) - 2) + [False]
        self.rec_layers = rec_layers

        if len(rec_layers) != len(shape):
            raise ValueError("Must define recurrence for each layer")

        super(HessianRNN, self).__init__(shape, **kwargs)

        # add on recurrent weights
        if kwargs.get("load_weights", None) is None and np.any(rec_layers):
            self.W = np.concatenate(
                (self.W, self.init_weights([(self.shape[l], self.shape[l])
                                            for l in range(self.n_layers)
                                            if rec_layers[l]],
                                           **W_rec_params)))

    def compute_offsets(self):
        """Precompute offsets for layers in the overall parameter vector."""

        super(HessianRNN, self).compute_offsets()

        # add in offsets for recurrent weights
        offset = len(self.W)  # note: gets called before rec_W added
        for l in range(self.n_layers):
            if self.rec_layers[l]:
                self.offsets[(l, l)] = (
                    offset,
                    offset + self.shape[l] * self.shape[l],
                    offset + (self.shape[l] + 1) * self.shape[l])
                offset += (self.shape[l] + 1) * self.shape[l]

    def forward(self, input, params, deriv=False):
        """Compute activations for given input sequence and parameters.

        If deriv=True then also compute the derivative of the activations.
        """

        # input shape = [batch_size, seq_len, input_dim]
        # activations shape = [n_layers, batch_size, seq_len, layer_size]

        if callable(input):
            # reset the plant
            input.reset()
        elif input.ndim < 3:
            # then we've just been given a single sample (rather than batch)
            input = input[None, :, :]

        activations = [np.zeros((input.shape[0], input.shape[1], l),
                                dtype=self.dtype)
                       for l in self.shape]

        if deriv:
            d_activations = [None for l in self.layers]

        for l in self.layers:
            # reset any state in the nonlinearities
            l.reset()

        W_recs = [self.get_weights(params, (i, i))
                  for i in np.arange(self.n_layers)]
        for s in range(input.shape[1]):
            for i in range(self.n_layers):
                if i == 0:
                    # get the external input
                    if callable(input):
                        # call the plant with the output of the previous
                        # timestep to generate the next input
                        ff_input = input(activations[-1][:, s - 1])
                    else:
                        ff_input = input[:, s]
                else:
                    # compute feedforward input
                    ff_input = np.zeros_like(activations[i][:, s])
                    for pre in self.back_conns[i]:
                        W, b = self.get_weights(params, (pre, i))
                        ff_input += np.dot(activations[pre][:, s], W) + b

                # recurrent input
                if self.rec_layers[i]:
                    if s > 0:
                        rec_input = np.dot(activations[i][:, s - 1],
                                           W_recs[i][0])
                    else:
                        # apply bias input on first timestep
                        rec_input = W_recs[i][1]
                else:
                    rec_input = 0

                # apply activation function
                activations[i][:, s] = (
                    self.layers[i].activation(ff_input + rec_input))

                # compute derivative
                if deriv:
                    d_act = self.layers[i].d_activation(
                        activations[i][:, s] if
                        self.layers[i].use_activations
                        else ff_input + rec_input)[:, None, :]

                    if d_activations[i] is None:
                        d_activations[i] = d_act
                    else:
                        d_activations[i] = np.concatenate((d_activations[i],
                                                           d_act), axis=1)

        if deriv:
            return activations, d_activations

        return activations

    def error(self, W=None, inputs=None, targets=None):
        """Compute network error."""

        if callable(inputs):
            assert targets is None

            # run plant to get inputs/targets
            W = self.W if W is None else W
            self.forward(inputs, W)
            targets = inputs.get_targets()
            inputs = inputs.get_inputs()

        return super(HessianRNN, self).error(W, inputs, targets)

    def calc_grad(self):
        """Compute parameter gradient."""

        grad = np.zeros_like(self.W)
        deltas = [np.zeros((self.inputs.shape[0], l), dtype=self.dtype)
                  for l in self.shape]
        state_deltas = [None if not l.stateful else
                        np.zeros((self.inputs.shape[0], self.shape[i]),
                                 dtype=self.dtype)
                        for i, l in enumerate(self.layers)]
        W_recs = [self.get_weights(self.W, (l, l))
                  for l in np.arange(self.n_layers)]

        # backpropagate error
        for s in range(self.inputs.shape[1] - 1, -1, -1):
            for l in range(self.n_layers - 1, -1, -1):
                if l == self.n_layers - 1:
                    # derivative of loss
                    error = self.loss[1](self.activations[-1][:, s],
                                         self.targets[:, s])
                else:
                    # error from feedforward weights
                    error = np.zeros_like(deltas[l])
                    for post in self.conns[l]:
                        c_error = np.dot(deltas[post],
                                         self.get_weights(self.W,
                                                          (l, post))[0].T)
                        error += c_error

                        # feedforward gradient
                        offset, W_end, b_end = self.offsets[(l, post)]
                        grad[offset:W_end] += (
                            self.outer_sum(self.activations[l][:, s]
                                           if self.GPU_activations is None
                                           else [l, np.index_exp[:, s]],
                                           deltas[post]))
                        grad[W_end:b_end] += np.sum(deltas[post], axis=0)

                # add recurrent error
                if self.rec_layers[l]:
                    error += np.dot(deltas[l], W_recs[l][0].T)

                # compute deltas
                if not self.layers[l].stateful:
                    deltas[l] = self.J_dot(self.d_activations[l][:, s], error,
                                           transpose=True)
                else:
                    d_input = self.d_activations[l][:, s, ..., 0]
                    d_state = self.d_activations[l][:, s, ..., 1]
                    d_output = self.d_activations[l][:, s, ..., 2]

                    state_deltas[l] += self.J_dot(d_output, error,
                                                  transpose=True)
                    deltas[l] = self.J_dot(d_input, state_deltas[l],
                                           transpose=True)
                    state_deltas[l] = self.J_dot(d_state, state_deltas[l],
                                                 transpose=True)

                # gradient for recurrent weights
                if self.rec_layers[l]:
                    offset, W_end, b_end = self.offsets[(l, l)]
                    if s > 0:
                        grad[offset:W_end] += (
                            self.outer_sum(self.activations[l][:, s - 1]
                                           if self.GPU_activations is None
                                           else [l, np.index_exp[:, s - 1]],
                                           deltas[l]))
                    else:
                        # put remaining gradient into initial bias
                        grad[W_end:b_end] = np.sum(deltas[l], axis=0)

        # divide by batchsize
        grad /= self.inputs.shape[0]

        return grad

    def calc_G(self, v, damping=0, output=None):
        """Compute Gauss-Newton matrix-vector product."""

        if output is None:
            Gv = np.zeros(self.W.size, dtype=self.dtype)
        else:
            Gv = output
            Gv[:] = 0

        sig_len = self.inputs.shape[1]

        # R forward pass
        R_inputs = [np.zeros(self.activations[i].shape, dtype=self.dtype)
                    for i in np.arange(self.n_layers)]
        R_states = [None if not l.stateful else
                    np.zeros((self.inputs.shape[0], self.shape[i]),
                             dtype=self.dtype)
                    for i, l in enumerate(self.layers)]
        R_activations = [None for _ in self.shape]
        R_outputs = np.zeros_like(self.activations[-1])
        v_recs = [self.get_weights(v, (l, l))
                  for l in np.arange(self.n_layers)]
        W_recs = [self.get_weights(self.W, (l, l))
                  for l in np.arange(self.n_layers)]

        for s in np.arange(sig_len):
            for l in np.arange(self.n_layers):
                # input from feedforward connections
                if l > 0:
                    for pre in self.back_conns[l]:
                        vw, vb = self.get_weights(v, (pre, l))
                        Ww, _ = self.get_weights(self.W, (pre, l))
                        R_inputs[l][:, s] += (
                            np.dot(self.activations[pre][:, s], vw) + vb)
                        R_inputs[l][:, s] += np.dot(R_activations[pre], Ww)

                # recurrent input
                if self.rec_layers[l]:
                    if s == 0:
                        # bias input on first step
                        R_inputs[l][:, s] += v_recs[l][1]
                    else:
                        R_inputs[l][:, s] += (
                            np.dot(self.activations[l][:, s - 1],
                                   v_recs[l][0]) +
                            np.dot(R_activations[l], W_recs[l][0]))

                if not self.layers[l].stateful:
                    R_activations[l] = self.J_dot(self.d_activations[l][:, s],
                                                  R_inputs[l][:, s])
                else:
                    d_input = self.d_activations[l][:, s, ..., 0]
                    d_state = self.d_activations[l][:, s, ..., 1]
                    d_output = self.d_activations[l][:, s, ..., 2]

                    R_states[l] = self.J_dot(d_state, R_states[l])
                    R_states[l] += self.J_dot(d_input, R_inputs[l][:, s])
                    R_activations[l] = self.J_dot(d_output, R_states[l])


            # copy output activations so we can reuse to compute error in
            # backwards pass
            R_outputs[:, s] = R_activations[-1]

        # R backward pass
        R_deltas = [np.zeros((self.inputs.shape[0], l), dtype=self.dtype)
                    for l in self.shape]
        R_states = [None if not l.stateful else
                    np.zeros((self.inputs.shape[0], self.shape[i]),
                             dtype=self.dtype)
                    for i, l in enumerate(self.layers)]
        for s in np.arange(sig_len - 1, -1, -1):
            for l in np.arange(self.n_layers - 1, -1, -1):
                if l == self.n_layers - 1:
                    # output layer
                    R_error = (R_outputs[:, s] *
                               self.loss[2](self.activations[l][:, s],
                                            self.targets[:, s]))
                else:
                    # error from feedforward connections
                    R_error = np.zeros_like(self.activations[l][:, s])
                    for post in self.conns[l]:
                        W, _ = self.get_weights(self.W, (l, post))
                        R_error += np.dot(R_deltas[post], W.T)

                        # feedforward gradient
                        offset, W_end, b_end = self.offsets[(l, post)]
                        Gv[offset:W_end] += (
                            self.outer_sum(self.activations[l][:, s]
                                           if self.GPU_activations is None
                                           else [l, np.index_exp[:, s]],
                                           R_deltas[post]))
                        Gv[W_end:b_end] += np.sum(R_deltas[post], axis=0)

                # add recurrent error
                if self.rec_layers[l]:
                    R_error += np.dot(R_deltas[l], W_recs[l][0].T)

                # compute deltas
                if not self.layers[l].stateful:
                    d_output = self.d_activations[l][:, s]
                    R_deltas[l] = self.J_dot(self.d_activations[l][:, s],
                                             R_error,
                                             transpose=True)
                else:
                    # TODO: optimize identity d_state/d_input (None)

                    d_input = self.d_activations[l][:, s, ..., 0]
                    d_state = self.d_activations[l][:, s, ..., 1]
                    d_output = self.d_activations[l][:, s, ..., 2]

                    R_states[l] += self.J_dot(d_output, R_error,
                                              transpose=True)
                    R_deltas[l] = self.J_dot(d_input, R_states[l],
                                             transpose=True)
                    R_states[l] = self.J_dot(d_state, R_states[l],
                                             transpose=True)

                # apply structural damping
                # TODO: fix this to work with stateful nonlinearities (should
                # include more than just d_output?)
                R_deltas[l] += self.J_dot(d_output,
                                          damping * self.struc_damping *
                                          R_inputs[l][:, s])

                # recurrent gradient
                if self.rec_layers[l]:
                    offset, W_end, b_end = self.offsets[(l, l)]
                    if s > 0:
                        Gv[offset:W_end] += (
                            self.outer_sum(self.activations[l][:, s - 1]
                                           if self.GPU_activations is None
                                           else [l, np.index_exp[:, s - 1]],
                                           R_deltas[l]))
                    else:
                        Gv[W_end:b_end] = np.sum(R_deltas[l], axis=0)

        Gv /= self.inputs.shape[0]

        Gv += damping * v  # Tikhonov damping

        return Gv
