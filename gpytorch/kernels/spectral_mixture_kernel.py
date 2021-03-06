from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import math
import torch
from gpytorch.kernels import Kernel

logger = logging.getLogger()


class SpectralMixtureKernel(Kernel):
    def __init__(
        self,
        n_mixtures,
        n_dims=1,
        log_mixture_weight_prior=None,
        log_mixture_mean_prior=None,
        log_mixture_scale_prior=None,
        active_dims=None,
    ):
        self.n_mixtures = n_mixtures
        self.n_dims = n_dims
        if (
            log_mixture_mean_prior is not None
            or log_mixture_scale_prior is not None
            or log_mixture_weight_prior is not None
        ):
            logger.warning("Priors not implemented for SpectralMixtureKernel")

        super(SpectralMixtureKernel, self).__init__(active_dims=active_dims)
        self.register_parameter(name="log_mixture_weights", parameter=torch.nn.Parameter(torch.zeros(self.n_mixtures)))
        self.register_parameter(
            name="log_mixture_means", parameter=torch.nn.Parameter(torch.zeros(self.n_mixtures, self.n_dims))
        )
        self.register_parameter(
            name="log_mixture_scales", parameter=torch.nn.Parameter(torch.zeros(self.n_mixtures, self.n_dims))
        )

    def initialize_from_data(self, train_x, train_y, **kwargs):
        if not torch.is_tensor(train_x) or not torch.is_tensor(train_y):
            raise RuntimeError("train_x and train_y should be tensors")
        if train_x.ndimension() == 1:
            train_x = train_x.unsqueeze(-1)
        if train_x.ndimension() == 2:
            train_x = train_x.unsqueeze(0)

        train_x_sort = train_x.sort(1)[0]
        max_dist = train_x_sort[:, -1, :] - train_x_sort[:, 0, :]
        min_dist_sort = (train_x_sort[:, 1:, :] - train_x_sort[:, :-1, :]).squeeze(0)
        min_dist = torch.zeros(1, self.n_dims)
        for ind in range(self.n_dims):
            min_dist[:, ind] = min_dist_sort[(torch.nonzero(min_dist_sort[:, ind]))[0], ind]

        # Inverse of lengthscales should be drawn from truncated Gaussian | N(0, max_dist^2) |
        self.log_mixture_scales.data.normal_().mul_(max_dist).abs_().pow_(-1).log_()
        # Draw means from Unif(0, 0.5 / minimum distance between two points)
        self.log_mixture_means.data.uniform_().mul_(0.5).div_(min_dist).log_()
        # Mixture weights should be roughly the stdv of the y values divided by the number of mixtures
        self.log_mixture_weights.data.fill_(train_y.std() / self.n_mixtures).log_()

    def forward(self, x1, x2):
        batch_size, n, n_dims = x1.size()
        _, m, _ = x2.size()
        if not n_dims == self.n_dims:
            raise RuntimeError("The number of dimensions doesn't match what was supplied!")

        mixture_weights = self.log_mixture_weights.view(self.n_mixtures, 1, 1, 1).exp()
        mixture_means = self.log_mixture_means.view(self.n_mixtures, 1, 1, 1, self.n_dims).exp()
        mixture_scales = self.log_mixture_scales.view(self.n_mixtures, 1, 1, 1, self.n_dims).exp()
        distance = (x1.unsqueeze(-2) - x2.unsqueeze(-3)).abs()  # distance = x^(i) - z^(i)

        exp_term = (distance * mixture_scales).pow_(2).mul_(-2 * math.pi ** 2)
        cos_term = (distance * mixture_means).mul_(2 * math.pi)
        res = exp_term.exp_() * cos_term.cos_()

        # Product over dimensions
        res = res.prod(-1)

        # Sum over mixtures
        res = (res * mixture_weights).sum(0)
        return res
