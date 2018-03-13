import tensorflow as tf
import numpy as np
from collections import OrderedDict
import os
import glob
import cv2


rng = np.random.RandomState(2017)


def np_load_frame(filename, resize_height, resize_width):
    image_decoded = cv2.imread(filename)
    image_resized = cv2.resize(image_decoded, (resize_width, resize_height))
    image_resized = image_resized.astype(dtype=np.float32)
    image_resized = (image_resized / 127.5) - 1.0
    return image_resized


class DataLoader(object):
    def __init__(self, video_folder, resize_height=256, resize_width=256):
        self.dir = video_folder
        self.videos = {}
        self._resize_height = resize_height
        self._resize_width = resize_width
        self.setup()

    def __call__(self, batch_size, time_steps, num_pred=1):
        video_info_list = list(self.videos.values())
        num_videos = len(video_info_list)

        clip_length = time_steps + num_pred
        resize_height, resize_width = self._resize_height, self._resize_width

        def video_clip_generator():
            v_id = -1
            while True:
                v_id = (v_id + 1) % num_videos

                video_info = video_info_list[v_id]
                start = rng.randint(0, video_info['length'] - clip_length)
                video_clip = []
                for frame_id in range(start, start + clip_length):
                    video_clip.append(np_load_frame(video_info['frame'][frame_id], resize_height, resize_width))
                video_clip = np.concatenate(video_clip, axis=2)

                yield video_clip

        # video clip paths
        dataset = tf.data.Dataset.from_generator(generator=video_clip_generator,
                                                 output_types=tf.float32,
                                                 output_shapes=[resize_height, resize_width, clip_length * 3])
        print('generator dataset, {}'.format(dataset))
        dataset = dataset.prefetch(buffer_size=1000)
        dataset = dataset.shuffle(buffer_size=1000).batch(batch_size)
        print('epoch dataset, {}'.format(dataset))

        return dataset

    def __getitem__(self, video_name):
        assert video_name in self.videos.keys(), 'video = {} is not in {}!'.format(video_name, self.videos.keys())
        return self.videos[video_name]

    def setup(self):
        videos = glob.glob(os.path.join(self.dir, '*'))
        for video in sorted(videos):
            video_name = video.split('/')[-1]
            self.videos[video_name] = {}
            self.videos[video_name]['path'] = video
            self.videos[video_name]['frame'] = glob.glob(os.path.join(video, '*.jpg'))
            self.videos[video_name]['frame'].sort()
            self.videos[video_name]['length'] = len(self.videos[video_name]['frame'])

    def get_video_clips(self, video, start, end):
        # assert video in self.videos, 'video = {} must in {}!'.format(video, self.videos.keys())
        # assert start >= 0, 'start = {} must >=0!'.format(start)
        # assert end <= self.videos[video]['length'], 'end = {} must <= {}'.format(video, self.videos[video]['length'])

        batch = []
        for i in range(start, end):
            image = np_load_frame(self.videos[video]['frame'][i], self._resize_height, self._resize_width)
            batch.append(image)

        return np.concatenate(batch, axis=2)

    # def get_video_clips(self, video_name, start, end):
    #     video_idx = np.arange(start, end)
    #     video_clip = np.empty(shape=[self._resize_height, self._resize_height, 3*len(video_idx)], dtype=np.float32)
    #     for idx, v_idx in enumerate(video_idx):
    #         filename = self.videos[video_name]['frame'][v_idx]
    #         video_clip[..., idx*3:(idx+1)*3] = np_load_frame(filename, self._resize_height, self._resize_width)
    #
    #     return video_clip


def log10(t):
    """
    Calculates the base-10 log of each element in t.

    @param t: The tensor from which to calculate the base-10 log.

    @return: A tensor with the base-10 log of each element in t.
    """

    numerator = tf.log(t)
    denominator = tf.log(tf.constant(10, dtype=numerator.dtype))
    return numerator / denominator


def psnr_error(gen_frames, gt_frames):
    """
    Computes the Peak Signal to Noise Ratio error between the generated images and the ground
    truth images.

    @param gen_frames: A tensor of shape [batch_size, height, width, 3]. The frames generated by the
                       generator model.
    @param gt_frames: A tensor of shape [batch_size, height, width, 3]. The ground-truth frames for
                      each frame in gen_frames.

    @return: A scalar tensor. The mean Peak Signal to Noise Ratio error over each frame in the
             batch.
    """
    shape = tf.shape(gen_frames)
    num_pixels = tf.to_float(shape[1] * shape[2] * shape[3])
    gt_frames = (gt_frames + 1.0) / 2.0
    gen_frames = (gen_frames + 1.0) / 2.0
    square_diff = tf.square(gt_frames - gen_frames)

    batch_errors = 10 * log10(1 / ((1 / num_pixels) * tf.reduce_sum(square_diff, [1, 2, 3])))
    return tf.reduce_mean(batch_errors)


def sharp_diff_error(gen_frames, gt_frames, channels=3):
    """
    Computes the Sharpness Difference error between the generated images and the ground truth
    images.

    @param gen_frames: A tensor of shape [batch_size, height, width, 3]. The frames generated by the
                       generator model.
    @param gt_frames: A tensor of shape [batch_size, height, width, 3]. The ground-truth frames for
                      each frame in gen_frames.
    @param channels: The number of channels, 3 is RGB and 1 is Gray, default is 3.

    @return: A scalar tensor. The Sharpness Difference error over each frame in the batch.
    """
    shape = tf.shape(gen_frames)
    num_pixels = tf.to_float(shape[1] * shape[2] * shape[3])

    # gradient difference
    # create filters [-1, 1] and [[1],[-1]] for diffing to the left and down respectively.
    # TODO: Could this be simplified with one filter [[-1, 2], [0, -1]]?
    pos = tf.constant(np.identity(channels), dtype=tf.float32)
    neg = -1 * pos
    filter_x = tf.expand_dims(tf.stack([neg, pos]), 0)  # [-1, 1]
    filter_y = tf.stack([tf.expand_dims(pos, 0), tf.expand_dims(neg, 0)])  # [[1],[-1]]
    strides = [1, 1, 1, 1]  # stride of (1, 1)
    padding = 'SAME'

    gen_dx = tf.abs(tf.nn.conv2d(gen_frames, filter_x, strides, padding=padding))
    gen_dy = tf.abs(tf.nn.conv2d(gen_frames, filter_y, strides, padding=padding))
    gt_dx = tf.abs(tf.nn.conv2d(gt_frames, filter_x, strides, padding=padding))
    gt_dy = tf.abs(tf.nn.conv2d(gt_frames, filter_y, strides, padding=padding))

    gen_grad_sum = gen_dx + gen_dy
    gt_grad_sum = gt_dx + gt_dy

    grad_diff = tf.abs(gt_grad_sum - gen_grad_sum)

    batch_errors = 10 * log10(1 / ((1 / num_pixels) * tf.reduce_sum(grad_diff, [1, 2, 3])))
    return tf.reduce_mean(batch_errors)


def diff_mask(gen_frames, gt_frames, min_value=-1, max_value=1):
    # normalize to [0, 1]
    delta = max_value - min_value
    gen_frames = (gen_frames - min_value) / delta
    gt_frames = (gt_frames - min_value) / delta

    gen_gray_frames = tf.image.rgb_to_grayscale(gen_frames)
    gt_gray_frames = tf.image.rgb_to_grayscale(gt_frames)

    diff = tf.abs(gen_gray_frames - gt_gray_frames)
    return diff


def load(saver, sess, ckpt_path):
    saver.restore(sess, ckpt_path)
    print("Restored model parameters from {}".format(ckpt_path))


def save(saver, sess, logdir, step):
    model_name = 'model.ckpt'
    checkpoint_path = os.path.join(logdir, model_name)
    if not os.path.exists(logdir):
        os.makedirs(logdir)
    saver.save(sess, checkpoint_path, global_step=step)
    print('The checkpoint has been created.')


# if __name__ == '__main__':
#     os.environ['CUDA_DEVICES_ORDER'] = "PCI_BUS_ID"
#     os.environ['CUDA_VISIBLE_DEVICES'] = '0'
#
#     data_loader = DataLoader('/home/liuwen/ssd/datasets/avenue/training/frames')
#     dataset, epoch_size = data_loader(10, 4, 1, 3, 1)
#
#     # debug
#     iteration = dataset.make_one_shot_iterator()
#     batch_video_clip_tensor = iteration.get_next()
#
#     config = tf.ConfigProto()
#     config.gpu_options.allow_growth = True
#     with tf.Session(config=config) as sess:
#         # batch_video_clip = sess.run(next(it))
#
#         for i in range(100):
#             batch_video_clip = sess.run(batch_video_clip_tensor)
#             # print(batch_video_clip.shape)
#
#             for vid, video_clip in enumerate(batch_video_clip):
#                 for fid, frame in enumerate(video_clip):
#                     print(i, vid, fid)
#                     cv2.imshow('visualization', frame + 0.5)
#                     cv2.waitKey(100)


