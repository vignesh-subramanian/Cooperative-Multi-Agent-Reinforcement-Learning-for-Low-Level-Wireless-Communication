############################################################
#
#  Basic Learning Transmitter
#  Shane Barratt <stbarratt@gmail.com>
#
#  Simulates a learning transmitter with a fixed receiver.
#
############################################################

from environment import Environment
import tensorflow as tf
import numpy as np
import IPython as ipy
import itertools
import matplotlib.pyplot as plt
import time

# normalized constant initializer for NN weights from cs 294-112 code
def normc_initializer(std=1.0):
    def _initializer(shape, dtype=None, partition_info=None):
        out = np.random.randn(*shape).astype(np.float32)
        out *= std / np.sqrt(np.square(out).sum(axis=0, keepdims=True))
        return tf.constant(out)
    return _initializer

class NeuralTransmitter(object):
    def __init__(self, n_bits=2, num_hidden_per_layer=[64, 32], steps_per_episode=32, stepsize=5e-3, action_dim=2, desired_kl=1e-1):

        self.n_hidden_layers = len(num_hidden_per_layer)
        self.num_hidden_per_layer = num_hidden_per_layer
        self.desired_kl = desired_kl

        self.n_bits = n_bits
        self.action_dim = action_dim
        self.steps_per_episode = steps_per_episode
        self.stepsize = stepsize

        self.step = 0 # current step

        # saved xs, actions and advantages
        self.reset_accum()

        # Network
        self.sy_x = tf.placeholder(tf.float32, [None, self.n_bits]) # -1 or 1
        self.sy_actions = tf.placeholder(tf.float32, [None, self.action_dim]) # x actions for gradient calculation
        self.sy_adv = tf.placeholder(tf.float32, [None]) # advantages for gradient computation
        self.sy_stepsize = tf.placeholder(shape=[], dtype=tf.float32) # stepsize for gradient step
        self.sy_batch_size = tf.placeholder(tf.int32, [])

        self.sy_old_action_means = tf.placeholder(tf.float32, [None, self.action_dim])
        self.sy_old_logstds = tf.placeholder(tf.float32, [None, self.action_dim])

        # Hidden Layers
        self.layers = [self.sy_x]
        for i in range(self.n_hidden_layers):
            h = tf.contrib.layers.fully_connected(
                inputs = self.layers[-1],
                num_outputs = self.num_hidden_per_layer[i],
                activation_fn = tf.nn.relu, # relu activation for hidden layer
                weights_initializer = normc_initializer(1.0),
                biases_initializer = tf.constant_initializer(.1)
            )
            self.layers.append(h)

        self.h_last = self.layers[-1]

        # Outputs
        self.action_means = tf.contrib.layers.fully_connected(
                inputs = self.h_last,
                num_outputs = self.action_dim,
                activation_fn = None,
                weights_initializer = normc_initializer(.2),
                biases_initializer = tf.constant_initializer(0.0)
        )
        self.x_y_logstds = tf.Variable(tf.ones(shape=self.action_dim))
        self.x_y_logstds = tf.reshape(self.x_y_logstds, [-1, 1])
        self.x_y_logstds = tf.tile(self.x_y_logstds, [1, self.sy_batch_size])
        self.x_y_logstds = tf.transpose(self.x_y_logstds)

        self.x_y_distr = tf.contrib.distributions.MultivariateNormalDiag(self.action_means, tf.exp(self.x_y_logstds))
        self.action_sample = self.x_y_distr.sample()

        self.x_y_old_distr = tf.contrib.distributions.MultivariateNormalDiag(self.sy_old_action_means, tf.exp(self.sy_old_logstds))

        self.kl = tf.reduce_mean(tf.contrib.distributions.kl(self.x_y_distr, self.x_y_old_distr))

        # Compute log-probabilities for gradient estimation
        self.x_y_logprob = self.x_y_distr.log_prob(self.sy_actions)
        self.sy_surr = - tf.reduce_mean(self.sy_adv * self.x_y_logprob)

        self.update_op = tf.train.AdamOptimizer(self.sy_stepsize).minimize(self.sy_surr)

        self.sess = tf.Session()
        self.sess.run(tf.global_variables_initializer())

    def reset_accum(self):
        self.xs_accum = np.empty((0, self.n_bits))
        self.actions_accum = np.empty((0, self.action_dim))
        self.adv_accum = np.empty(0)

    def policy_update(self):
        print ("updating policy")

        old_action_means, old_logstds = self.sess.run([self.action_means, self.x_y_logstds], feed_dict={
                self.sy_x: self.xs_accum,
                self.sy_batch_size: self.actions_accum.shape[0]
        })

        _ = self.sess.run([self.update_op], feed_dict={
                self.sy_x: self.xs_accum,
                self.sy_actions: self.actions_accum,
                self.sy_adv: self.adv_accum,
                self.sy_stepsize: self.stepsize,
                self.sy_batch_size: self.actions_accum.shape[0]
        })

        kl = self.sess.run([self.kl], feed_dict={
                self.sy_x: self.xs_accum,
                self.sy_batch_size: self.actions_accum.shape[0],
                self.sy_old_action_means: old_action_means,
                self.sy_old_logstds: old_logstds
        })[0]

        print ('KL: %.6f' % kl)
        if kl > self.desired_kl * 2:
            self.stepsize /= 1.5
            print ('stepsize -> %.6f' % self.stepsize)
        elif kl < self.desired_kl / 2:
            self.stepsize *= 1.5
            print ('stepsize -> %.6f' % self.stepsize)
        else:
            print ('stepsize OK')

        self.reset_accum()

    def transmit(self, x_input, evaluate=False):
        self.step += 1

        # convert input into proper format (e.g. x=[1 0] --> [1 -1])
        x_input = 2 * (x_input - .5)

        # run policy
        if evaluate:
            action = self.sess.run([self.action_means], feed_dict={
                    self.sy_x: x_input,
                    self.sy_batch_size: 1
            })[0]
        else:
            action = self.sess.run([self.action_sample], feed_dict={
                    self.sy_x: x_input,
                    self.sy_batch_size: 1
            })[0]

            self.xs_accum = np.r_[self.xs_accum, x_input]
            self.actions_accum = np.r_[self.actions_accum, action]

        return action[0]

    def receive_reward(self, rew):
        self.adv_accum = np.r_[self.adv_accum, rew]

        # If episode over, update policy and reset
        if self.step >= self.steps_per_episode:
            self.policy_update()
            self.step = 0

    def constellation(self, iteration=0, groundtruth=None):
        """
        Plots a constellation diagram. (https://en.wikipedia.org/wiki/Constellation_diagram)
        """
        bitstrings = list(itertools.product([0, 1], repeat=self.n_bits))

        plt.figure(figsize=(4, 4))
        size = 5

        for bs in bitstrings:
            x,y = self.transmit(np.array(bs)[None], evaluate=True)
            plt.scatter(x, y, label=str(bs))
            plt.annotate(str(bs), (x, y), size=size)
        plt.axvline(0)
        plt.axhline(0)
        plt.xlim([-2., 2.])
        plt.ylim([-2., 2.])

        if groundtruth:
            for k in groundtruth.keys():
                x_gt, y_gt = groundtruth[k]
                plt.scatter(x_gt, y_gt, s=size, color='purple')
                plt.annotate(''.join([str(b) for b in k]), (x_gt, y_gt), size=size)

        plt.savefig('figures/%d.png' % iteration)
        plt.close()

# Given a decoding map, return the closest bitstring
def rx_decode(rx_inp, decoding_map):
    rx_out, dist = None, float("inf")
    for k in decoding_map.keys():
        d = np.linalg.norm(rx_inp - decoding_map[k], ord=2)
        if d < dist:
            rx_out = np.array(k)
            dist = d
    return rx_out

def run_simulation(n_bits, l, seed, steps_per_episode, stepsize, num_hidden_per_layer, decoding_map, sigma, desired_kl):

    # set seed
    tf.set_random_seed(seed)
    np.random.seed(seed)

    # instantiate environment
    env = Environment(n_bits=n_bits, l=l, sigma=sigma)
    # instantiate transmitter
    nt = NeuralTransmitter(n_bits=n_bits, steps_per_episode=steps_per_episode, stepsize=stepsize, num_hidden_per_layer=num_hidden_per_layer, desired_kl=desired_kl
        )

    # training and evaluation
    for i in range(1000):
        rew_per_ep = 0.0
        start = time.time()
        for _ in range(steps_per_episode):
            # tx
            tx_inp = env.get_input_transmitter()
            tx_out = nt.transmit(tx_inp[None])
            env.output_transmitter(tx_out)

            # rx
            rx_inp = env.get_input_receiver()
            rx_out = rx_decode(rx_inp, decoding_map)
            env.output_receiver(rx_out)

            # rewards
            tx_reward = env.reward_transmitter()
            rx_reward = env.reward_receiver()
            nt.receive_reward(tx_reward)
            rew_per_ep += tx_reward * 1.0/steps_per_episode

        end = time.time()

        # Evaluate on all bitstrings
        bitstrings = list(itertools.product([0, 1], repeat=n_bits))
        rew = 0.0
        for b in bitstrings:
            tx_out = nt.transmit(np.array(b)[None], evaluate=True)
            rx_inp = tx_out
            rx_out = rx_decode(rx_inp, decoding_map)
            rew += np.linalg.norm(np.array(b) - rx_out, ord=1)

        print ("\n######## Epoch %d ########" % i)
        print ("rew_per_ep:", rew_per_ep)
        print ("bits incorrect / %d:" % (n_bits*2**(n_bits)), rew)
        print ("wall clock time: %.4f ms" % ((end - start)*1000))

        if i % 10 == 0:
            nt.constellation(iteration=i, groundtruth=decoding_map)

if __name__ == '__main__':
    # page 570 of (Proakis, Salehi)
    psk = {
        (0, 0): 1.0/np.sqrt(2)*np.array([1, 1]),
        (0, 1): 1.0/np.sqrt(2)*np.array([-1, 1]),
        (1, 0): 1.0/np.sqrt(2)*np.array([1, -1]),
        (1, 1): 1.0/np.sqrt(2)*np.array([-1,-1])
    }

    qam16 = {
        (0, 0, 0, 0): 0.5/np.sqrt(2)*np.array([1, 1]),
        (0, 0, 0, 1): 0.5/np.sqrt(2)*np.array([3, 1]),
        (0, 0, 1, 0): 0.5/np.sqrt(2)*np.array([1, 3]),
        (0, 0, 1, 1): 0.5/np.sqrt(2)*np.array([3, 3]),
        (0, 1, 0, 0): 0.5/np.sqrt(2)*np.array([1, -1]),
        (0, 1, 0, 1): 0.5/np.sqrt(2)*np.array([1, -3]),
        (0, 1, 1, 0): 0.5/np.sqrt(2)*np.array([3, -1]),
        (0, 1, 1, 1): 0.5/np.sqrt(2)*np.array([3, -3]),
        (1, 0, 0, 0): 0.5/np.sqrt(2)*np.array([-1, 1]),
        (1, 0, 0, 1): 0.5/np.sqrt(2)*np.array([-1, 3]),
        (1, 0, 1, 0): 0.5/np.sqrt(2)*np.array([-3, 1]),
        (1, 0, 1, 1): 0.5/np.sqrt(2)*np.array([-3, 3]),
        (1, 1, 0, 0): 0.5/np.sqrt(2)*np.array([-1, -1]),
        (1, 1, 0, 1): 0.5/np.sqrt(2)*np.array([-3, -1]),
        (1, 1, 1, 0): 0.5/np.sqrt(2)*np.array([-1, -3]),
        (1, 1, 1, 1): 0.5/np.sqrt(2)*np.array([-3, -3])
    }

    general_params = dict(stepsize=1e-3, desired_kl=3e-2, steps_per_episode=512)
    params = [
        dict(seed=0, n_bits=4, decoding_map=qam16, l=.1, sigma=.2,num_hidden_per_layer=[64, 20], **general_params),
        dict(seed=0, n_bits=2, decoding_map=psk, l=.1, sigma=.2, num_hidden_per_layer=[64, 20], **general_params),
    ]

    for param in params:
        run_simulation(**param)

