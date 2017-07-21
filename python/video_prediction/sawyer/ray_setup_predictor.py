# starting the client

import tensorflow as tf
import imp
import numpy as np
import pdb

from PIL import Image
import os

from datetime import datetime
import ray
import time
@ray.remote(num_gpus=1)
class LocalServer(object):
    def __init__(self, conf, local_batch_size):
        print 'making LocalServer'

        self.local_batch_size = local_batch_size

        self.conf = conf
        if 'prediction_model' in conf:
            Model = conf['prediction_model']
        else:
            from video_prediction.sawyer.prediction_train_sawyer import Model

        print 'using CUDA_VISIBLE_DEVICES=', ray.get_gpu_ids()[0]%4
        os.environ["CUDA_VISIBLE_DEVICES"] = str(ray.get_gpu_ids()[0]%4)

        from tensorflow.python.client import device_lib
        print device_lib.list_local_devices()

        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.9)

        # Make training session.
        self.sess = tf.InteractiveSession(config=tf.ConfigProto(gpu_options=gpu_options,
                                                           allow_soft_placement=True,
                                                           log_device_placement=False))

        print '-------------------------------------------------------------------'
        print 'verify current settings!! '
        for key in conf.keys():
            print key, ': ', conf[key]
        print '-------------------------------------------------------------------'

        print 'Constructing multi gpu model for control...'

        if 'single_view' in conf:
            numcam = 1
        else:
            numcam = 2
        self.start_images_pl = tf.placeholder(tf.float32, name='images',  # with zeros extension
                                        shape=(local_batch_size, conf['sequence_length'], 64, 64, 3*numcam))
        self.actions_pl = tf.placeholder(tf.float32, name='actions',
                                        shape=(local_batch_size,conf['sequence_length'], 4))
        self.start_states_pl = tf.placeholder(tf.float32, name='states',
                                              shape=(local_batch_size,conf['context_frames'], 3))
        self.pix_distrib_1_pl = tf.placeholder(tf.float32, shape=(local_batch_size, conf['context_frames'], 64, 64, numcam))
        self.pix_distrib_2_pl = tf.placeholder(tf.float32, shape=(local_batch_size, conf['context_frames'], 64, 64, numcam))

        with tf.variable_scope('model', reuse=None):
            self.model = Model(conf, self.start_images_pl, self.actions_pl, self.start_states_pl,
                               pix_distrib=self.pix_distrib_1_pl)

        self.sess.run(tf.initialize_all_variables())

        saver = tf.train.Saver(tf.get_collection(tf.GraphKeys.VARIABLES), max_to_keep=0)
        saver.restore(self.sess, conf['pretrained_model'])
        print 'restore done. '


        self.sess.run(tf.initialize_all_variables())
        restore_vars = tf.get_default_graph().get_collection(name=tf.GraphKeys.VARIABLES, scope='model')
        # for var in restore_vars:
        #     print var.name, var.get_shape()
        saver = tf.train.Saver(restore_vars, max_to_keep=0)
        saver.restore(self.sess, conf['pretrained_model'])

    def get_ip_address(self):
        time.sleep(0.01)
        return ray.services.get_node_ip_address()

    # def predict(self, input_images=None, input_one_hot_images1=None, input_state=None, input_actions=None):  ##  original line
    def predict(self):
        ######################
        input_images = np.zeros(shape=(self.local_batch_size, 15, 64, 64, 3), dtype=np.float32)
        input_one_hot_images1 = tf.zeros(shape=(self.local_batch_size, 2, 64, 64, 1), dtype=np.float32)
        input_state = np.zeros(shape=(self.local_batch_size, 2, 3), dtype=np.float32)
        input_actions = np.zeros(shape=(self.local_batch_size, 15, 4), dtype=np.float32)
        #######################

        t_startiter = datetime.now()

        feed_dict = {}

        feed_dict[self.start_images_pl] = input_images
        feed_dict[self.start_states_pl] = input_state
        feed_dict[self.actions_pl] = input_actions

        feed_dict[self.pix_distrib_1_pl] = input_one_hot_images1
        if 'ndesig' in self.conf:
            print 'evaluating 2 pixdistrib..'
            feed_dict[self.pix_distrib_2_pl] = input_one_hot_images2

            gen_images, gen_distrib1, gen_distrib2, gen_states = self.sess.run([self.model.gen_images,
                                                                         self.model.gen_distrib1,
                                                                         self.model.gen_distrib2,
                                                                         self.model.gen_states],
                                                                                feed_dict)
        else:
            gen_distrib2 = None
            gen_images, gen_distrib1, gen_states = self.sess.run([self.model.gen_images,
                                                             self.model.gen_distrib1,
                                                             self.model.gen_states,
                                                            ],
                                                           feed_dict)

        print 'time for evaluating {0} actions: {1}'.format(
            self.conf['batch_size'],
            (datetime.now() - t_startiter).seconds + (datetime.now() - t_startiter).microseconds / 1e6)


        # return gen_images, gen_distrib1, gen_distrib2, gen_states !!!!!!!!!!!!!!!!!!!


def setup_predictor(netconf, ngpu, redis_address):

    if redis_address == "":
        ray.init(num_gpus=ngpu)
        print 'using num gpus', ngpu
    else:
        ray.init(redis_address=redis_address)
        print 'using redis_address', redis_address

    local_bsize = np.floor(netconf['batch_size']/ngpu).astype(np.int32)
    new_batch_size = local_bsize * ngpu
    workers = []

    startind = []
    endind = []

    start_counter = 0
    for i in range(ngpu):
        workers.append(LocalServer.remote(netconf, local_bsize))

        startind.append(start_counter)
        endind.append(start_counter + local_bsize)
        print 'indices for gpu {0}: {1} to {2}'.format(i, startind[-1], endind[-1])
        start_counter += local_bsize

    print 'IP addresses of actors', ray.get([a.get_ip_address.remote() for a in workers])

    # def predictor_func(input_images=None,
    #                    input_one_hot_images1=None,
    #                    input_one_hot_images2=None,
    #                    input_state=None,
    #                    input_actions=None):

    def predictor_func():  # delete later

        result_list = []
        for igpu in range(ngpu):
            # result = workers[igpu].predict.remote(
            #                    input_images[startind[igpu]:endind[igpu]],
            #                    input_one_hot_images1[startind[igpu]:endind[igpu]],
            #                    input_state[startind[igpu]:endind[igpu]],
            #                    input_actions[startind[igpu]:endind[igpu]]
            #                    )

            result = workers[igpu].predict.remote()  ## delete later!

        ##### original code:
        #     result_list.append(result)
        #
        # gen_image_list = []
        # gen_distrib1_list = []
        # gen_distrib2_list = []
        # gen_states_list = []
        #
        # result_list = ray.get(result_list)  # do computation in parallel
        #
        # for igpu in range(ngpu):
        #     # create lists of length ngpu of lists of length sequence_length
        #     gen_images, gen_distrib1, gen_distrib2, gen_states = result_list[igpu]
        #
        #     gen_image_list.append(gen_images)
        #     gen_distrib1_list.append(gen_distrib1)
        #     if 'ndesig' in netconf:
        #         gen_distrib2_list.append(gen_distrib2)
        #     gen_states_list.append(gen_states)
        #
        # gen_images = []
        # gen_distrib1 = []
        # gen_distrib2 = []
        # gen_states = []
        #
        # for t in range(netconf['sequence_length']-1):
        #     gen_images.append(np.concatenate([gi[t] for gi in gen_image_list]))
        #     gen_distrib1.append(np.concatenate([g[t] for g in gen_distrib1_list]))
        #     if 'ndesig' in netconf:
        #         gen_distrib2.append(np.concatenate([g[t] for g in gen_distrib2_list]))
        #     else:
        #         gen_distrib2 = None
        #     gen_states.append(np.concatenate([s[t] for s in gen_states_list]))
        #
        #
        # return gen_images, gen_distrib1, gen_distrib2, gen_states

    return predictor_func


if __name__ == '__main__':
    conffile = '/home/frederik/Documents/catkin_ws/src/lsdc/experiments/cem_exp/benchmarks_sawyer/predprop_1stimg_bckgd/conf.py'
    netconf = imp.load_source('mod_hyper', conffile).configuration
    predfunc = setup_predictor(netconf,None, 1)
    pdb.set_trace()