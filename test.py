import pickle
import sys
import ast

import numpy as np
import matplotlib.pyplot as plt

from hessianff import HessianFF
from hessianrnn import HessianRNN


def test_xor():
    inputs = np.asarray([[0.1, 0.1], [0.1, 0.9], [0.9, 0.1], [0.9, 0.9]],
                        dtype=np.float32)
    targets = np.asarray([[0.1], [0.9], [0.9], [0.1]], dtype=np.float32)

    ff = HessianFF([2, 5, 1], debug=True, use_GPU=False)

    ff.run_batches(inputs, targets, CG_iter=2, max_epochs=40,
                   plotting=True)

    # using gradient descent (for comparison)
#     for i in range(10000):
#         if i % 100 == 0:
#             print "iteration", i
#         ff.gradient_descent(inputs, targets, l_rate=1)

    for i, t in zip(inputs, targets):
        print "input", i
        print "target", t
        print "output", ff.forward(i, ff.W)[-1]


def test_autoencoder():
    inputs = np.eye(10, dtype=np.float32)
    targets = inputs

    ff = HessianFF([10, 5, 10], neuron_types=["linear", "tanh", "softmax"],
                   error_type="ce",
                   debug=True, use_GPU=True)

#     for i in range(10000):
#         if i % 100 == 0:
#             print "iteration", i
#         ff.gradient_descent(inputs, targets, l_rate=1)

    ff.run_batches(inputs, targets, CG_iter=50, max_epochs=100,
                   plotting=True)


def test_mnist(model_args=None, run_args=None):
    # download dataset at http://deeplearning.net/data/mnist/mnist.pkl.gz
    with open("mnist.pkl", "rb") as f:
        train, _, test = pickle.load(f)

    if model_args is None:
        ff = HessianFF([28 * 28, 1024, 512, 256, 32, 10], error_type="ce",
                       neuron_types=["linear"] + ["logistic"] * 4 + ["softmax"],
                       use_GPU=False, debug=False)
    else:
        ff = HessianFF(**model_args)

    inputs = train[0]
    targets = np.zeros((inputs.shape[0], 10), dtype=np.float32)
    targets[np.arange(inputs.shape[0]), train[1]] = 1

    tmp = np.zeros((test[0].shape[0], 10), dtype=np.float32)
    tmp[np.arange(test[0].shape[0]), test[1]] = 1
    test = (test[0], tmp)

    if run_args is None:
        ff.run_batches(inputs, targets, CG_iter=250, batch_size=7500,
                       test=test, max_epochs=1000, plotting=True)
    else:
        ff.run_batches(inputs, targets, test=test, **run_args)

    output = ff.forward(test[0], ff.W)[-1]
    class_err = (np.sum(np.argmax(output, axis=1) !=
                        np.argmax(test[1], axis=1))
                 / float(len(test[0])))
    print "classification error", class_err


def test_cifar():
    # download dataset at http://www.cs.toronto.edu/~kriz/cifar.html
    train = [None, None]
    for i in range(1, 6):
        with open("cifar/data_batch_%s" % i, "rb") as f:
            batch = pickle.load(f)
        if i == 1:
            train[0] = batch["data"]
            train[1] = batch["labels"]
        else:
            train[0] = np.concatenate((train[0], batch["data"]), axis=0)
            train[1] = np.concatenate((train[1], batch["labels"]), axis=0)
    with open("cifar/test_batch", "rb") as f:
        batch = pickle.load(f)
    test = [None, None]
    test[0] = batch["data"]
    test[1] = batch["labels"]

    # take random patches from training set
    dim = 24
    tmp = np.zeros((train[0].shape[0], dim * dim * 3))
    for i, t in enumerate(train[0]):
        img = t.reshape(3, 32, 32)

#         plt.figure()
#         plt.imshow(img.swapaxes(0, 2), interpolation="none")
#         plt.show()

        x_offset = np.random.randint(32 - dim)
        y_offset = np.random.randint(32 - dim)
        img = img[:, x_offset:x_offset + dim, y_offset:y_offset + dim]

#         plt.figure()
#         plt.imshow(img.swapaxes(0, 2), interpolation="none")
#         plt.show()

        tmp[i] = np.ravel(img)
    train[0] = tmp

    # take centre patches from test set
    tmp = np.zeros((test[0].shape[0], dim * dim * 3))
    for i, t in enumerate(test[0]):
        img = t.reshape(3, 32, 32)
        offset = (32 - dim) / 2
        img = img[:, offset:offset + dim, offset:offset + dim]
        tmp[i] = np.ravel(img)
    test[0] = tmp

    train[0] = train[0].astype(np.float32)
    tmp = np.zeros((len(train[0]), 10), dtype=np.float32)
    tmp[np.arange(len(train[0])), train[1]] = 1.0
    train[1] = tmp

    test[0] = test[0].astype(np.float32)
    tmp = np.zeros((len(test[0]), 10), dtype=np.float32)
    tmp[np.arange(len(test[0])), test[1]] = 1.0
    test[1] = tmp

    ff = HessianFF([dim * dim * 3, 1024, 512, 256, 32, 10],
                   neuron_types=["linear"] + ["tanh"] * 4 + ["softmax"],
                   error_type="ce", use_GPU=True,
                   debug=False, load_weights=None)

    ff.run_batches(train[0], train[1], CG_iter=300, batch_size=5000,
                   test=test, max_epochs=1000, plotting=True)

    output = ff.forward(test[0], ff.W)[-1]
    class_err = np.mean(np.argmax(output, axis=1) !=
                        np.argmax(test[1], axis=1))
    print "classification error", class_err


def test_profile():
    np.random.seed(0)
    import cProfile
    import pstats

    cProfile.run("test_mnist(None, {'max_epochs':5, 'plotting':False, 'batch_size':7500})",
                 "profilestats")
    p = pstats.Stats("profilestats")
    p.strip_dirs().sort_stats('time').print_stats(20)


def test_GPU():
    import time

    ff = HessianFF(use_GPU=True, debug=False)
    ff.GPU_activations = None
    gpu = ff.outer_sum
    cpu = lambda a, b: np.ravel(np.einsum("ij,ik", a, b))

    vec_size = 11
    batch_size = 11
    reps = 100
    times = np.zeros((vec_size, batch_size, 2))
    for n in range(vec_size):
        for b in range(batch_size):
            x = np.random.randn(2 ** b, 2 ** n).astype(np.float32)
            y = np.random.randn(2 ** b, 2 ** n).astype(np.float32)

            start = time.time()
            for _ in range(reps):
                out = cpu(x, y)
            times[n, b, 0] = time.time() - start

            start = time.time()
            for _ in range(reps):
                out = gpu(x, y)
            times[n, b, 1] = time.time() - start

            print "n", n, "b", b, "times", times[n, b]

    print times
    print times[..., 1] > times[..., 0]


def test_integrator():
    n_inputs = 15
    sig_len = 50
    inputs = np.outer(np.linspace(0.1, 0.9, n_inputs),
                      np.ones(sig_len))[:, :, None]
    targets = np.outer(np.linspace(0.1, 0.9, n_inputs),
                       np.linspace(0, 1, sig_len))[:, :, None]
    inputs = inputs.astype(np.float32)
    targets = targets.astype(np.float32)

    test = (inputs, targets)

    rnn = HessianRNN(layers=[1, 10, 10, 1], struc_damping=0.0,
                     neuron_types="logistic", error_type="mse",
                     conns={0: [1, 2], 1: [2], 2: [3]},
                     use_GPU=False, debug=False)

    rnn.run_batches(inputs, targets, CG_iter=100, batch_size=None,
                    test=test, max_epochs=100, plotting=True)

    # using gradient descent (for comparison)
#     for i in range(10000):
#         if i % 100 == 0:
#             print "iteration", i
#         rnn.gradient_descent(inputs, targets, l_rate=0.1)

    plt.figure()
    plt.plot(inputs.squeeze().T)
    plt.title("inputs")

    plt.figure()
    plt.plot(targets.squeeze().T)
    plt.title("targets")

    outputs = rnn.forward(inputs, rnn.W)[-1]
    plt.figure()
    plt.plot(outputs.squeeze())
    plt.title("outputs")

    plt.show()

if len(sys.argv) < 2:
    test_xor()
else:
    if "test_%s" % sys.argv[1] in locals():
        locals()["test_%s" % sys.argv[1]](*[ast.literal_eval(a)
                                            for a in sys.argv[2:]])
    else:
        print "Unknown function (%s)" % sys.argv[1]
