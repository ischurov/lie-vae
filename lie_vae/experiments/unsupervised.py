import numpy as np
import torch
from torch.utils.data import DataLoader

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


class UnsupervisedExperiment:
    def __init__(self, *, log, model, optimizer, beta_schedule,
                 train_dataset, test_dataset, elbo_samples=1,
                 report_freq=1250, clip_grads=None, selective_clip=False,
                 batch_size=64):
        self.log = log
        self.model = model
        self.optimizer = optimizer
        self.beta_schedule = beta_schedule
        self.train_loader = DataLoader(
            train_dataset, batch_size=batch_size, shuffle=True, num_workers=10)
        self.test_loader = DataLoader(
            test_dataset, batch_size=batch_size, shuffle=True, num_workers=10)
        self.elbo_samples = elbo_samples
        self.clip_grads = clip_grads
        self.selective_clip = selective_clip
        self.report_freq = report_freq

    def test(self, loader):
        self.model.eval()
        losses = []
        for it, (item_label, rot_label, img_label) in enumerate(loader):
            img_label = img_label.to(device)
            recon, kl, kls = self.model.elbo(img_label, n=self.elbo_samples)
            losses.append((recon.mean().item(), kl.mean().item(),
                           *[x.mean().item() for x in kls]))
        return np.mean(losses, 0)

    def train(self, epoch):
        losses = []
        for it, batch in enumerate(self.train_loader):
            item_label, rot_label, img_label = batch
            self.model.train()
            img_label = img_label.to(device)
            recon, kl, kls = self.model.elbo(img_label, n=self.elbo_samples)

            global_it = epoch * len(self.train_loader) + it + 1
            beta = self.beta_schedule(global_it)

            loss = (recon + beta * kl).mean()

            if torch.isnan(kl).sum():
                raise RuntimeError("NaN KL")

            self.optimizer.zero_grad()
            loss.backward()
            if self.clip_grads:
                if self.selective_clip:
                    params = list(self.model.encoder.parameters()) \
                             + list(self.model.rep_group.parameters())
                else:
                    params = self.model.parameters()
                torch.nn.utils.clip_grad_norm_(params, self.clip_grads)
            self.optimizer.step()

            losses.append((recon.mean().item(), kl.mean().item(),
                           *[x.mean().item() for x in kls]))

            if (it + 1) % self.report_freq == 0 or \
                    it + 1 == len(self.train_loader):
                train_recon, train_kl, *train_kls = \
                    np.mean(losses[-self.report_freq:], 0)
                self.log.add_scalar('train_loss', train_recon + beta * train_kl,
                                    global_it)
                self.log.add_scalar('train_recon', train_recon, global_it)
                self.log.add_scalar('train_kl', train_kl, global_it)
                self.log.add_scalars(
                    'train_kls',
                    {'kl%d' % i: x for i, x in enumerate(train_kls)},
                    global_it)

                test_recon, test_kl, *test_kls = self.test(self.test_loader)
                self.log.add_scalar('test_loss', test_recon + beta * test_kl,
                                    global_it)
                self.log.add_scalar('test_recon', test_recon, global_it)
                self.log.add_scalar('test_kl', test_kl, global_it)
                self.log.add_scalars(
                    'test_kls',
                    {'kl%d' % i: x for i, x in enumerate(test_kls)},
                    global_it)

                self.log.add_scalar('beta', beta, global_it)
                print(('Epoch {} it {} train recon {:.4f} kl {:.4f}'
                       ' test recon {:.4f} kl {:.4f}')
                      .format(epoch, it+1, train_recon, train_kl,
                              test_recon, test_kl))