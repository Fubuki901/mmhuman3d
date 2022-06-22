from abc import ABCMeta, abstractmethod
from tkinter.messagebox import NO
from typing import Optional, Tuple, Union

import torch
import torch.nn.functional as F

import mmhuman3d.core.visualization.visualize_smpl as visualize_smpl
from mmhuman3d.core.conventions.keypoints_mapping import get_keypoint_idx, get_keypoint_idxs_by_part
from mmhuman3d.models.utils import FitsDict
from mmhuman3d.utils.geometry import (
    batch_rodrigues,
    estimate_translation,
    weak_perspective_projection,
    rotation_matrix_to_angle_axis,
)
from ..backbones.builder import build_backbone
from ..body_models.builder import build_body_model
from ..discriminators.builder import build_discriminator
from ..heads.builder import build_head
from ..losses.builder import build_loss
from ..necks.builder import build_neck
from ..registrants.builder import build_registrant
from .base_architecture import BaseArchitecture

from .base_architecture import BaseArchitecture


def set_requires_grad(nets, requires_grad=False):
    """Set requies_grad for all the networks.

    Args:
        nets (nn.Module | list[nn.Module]): A list of networks or a single
            network.
        requires_grad (bool): Whether the networks require gradients or not
    """
    if not isinstance(nets, list):
        nets = [nets]
    for net in nets:
        if net is not None:
            for param in net.parameters():
                param.requires_grad = requires_grad

def pose2rotmat(pred_pose):
    if len(pred_pose.shape) == 3:
        num_joints = pred_pose.shape[1]
        pred_pose = batch_rodrigues(pred_pose.view(-1,3)).view(-1, num_joints, 3, 3)
    return pred_pose

class SMPLXBodyModelEstimator(BaseArchitecture, metaclass=ABCMeta):
    """BodyModelEstimator Architecture.

    Args:
        backbone (dict | None, optional): Backbone config dict. Default: None.
        neck (dict | None, optional): Neck config dict. Default: None
        head (dict | None, optional): Regressor config dict. Default: None.
        disc (dict | None, optional): Discriminator config dict.
            Default: None.
        body_model_train (dict | None, optional): SMPL config dict during
            training. Default: None.
        body_model_test (dict | None, optional): SMPL config dict during
            test. Default: None.
        convention (str, optional): Keypoints convention. Default: "human_data"
        loss_keypoints2d (dict | None, optional): Losses config dict for
            2D keypoints. Default: None.
        loss_keypoints3d (dict | None, optional): Losses config dict for
            3D keypoints. Default: None.
        loss_vertex (dict | None, optional): Losses config dict for mesh
            vertices. Default: None
        loss_smplx_global_orient (dict | None, optional): Losses config dict for smplx
            global orient. Default: None
        loss_smplx_body_pose (dict | None, optional): Losses config dict for smplx
            body pose. Default: None
        loss_smplx_hand_pose (dict | None, optional): Losses config dict for smplx
            hand pose. Default: None
        loss_smplx_jaw_pose (dict | None, optional): Losses config dict for smplx
            jaw pose. Default: None
        loss_smplx_expression (dict | None, optional): Losses config dict for smplx
            expression. Default: None
        loss_smplx_betas (dict | None, optional): Losses config dict for smplx
            betas. Default: None
        loss_camera (dict | None, optional): Losses config dict for predicted
            camera parameters. Default: None
        loss_adv (dict | None, optional): Losses config for adversial
            training. Default: None.
        init_cfg (dict or list[dict], optional): Initialization config dict.
            Default: None.
    """

    def __init__(self,
                 backbone: Optional[Union[dict, None]] = None,
                 neck: Optional[Union[dict, None]] = None,
                 head: Optional[Union[dict, None]] = None,
                 disc: Optional[Union[dict, None]] = None,
                 body_model_train: Optional[Union[dict, None]] = None,
                 body_model_test: Optional[Union[dict, None]] = None,
                 convention: Optional[str] = 'human_data',
                 loss_keypoints2d: Optional[Union[dict, None]] = None,
                 loss_keypoints3d: Optional[Union[dict, None]] = None,
                 loss_smplx_global_orient: Optional[Union[dict, None]] = None,
                 loss_smplx_body_pose: Optional[Union[dict, None]] = None,
                 loss_smplx_hand_pose: Optional[Union[dict, None]] = None,
                 loss_smplx_jaw_pose: Optional[Union[dict, None]] = None,
                 loss_smplx_expression: Optional[Union[dict, None]] = None,
                 loss_smplx_betas: Optional[Union[dict, None]] = None,
                 loss_smplx_betas_prior: Optional[Union[dict, None]] = None,
                 loss_camera: Optional[Union[dict, None]] = None,
                 loss_adv: Optional[Union[dict, None]] = None,
                 init_cfg: Optional[Union[list, dict, None]] = None):
        super(SMPLXBodyModelEstimator, self).__init__(init_cfg)
        self.backbone = build_backbone(backbone)
        self.neck = build_neck(neck)
        self.head = build_head(head)
        self.disc = build_discriminator(disc)

        self.body_model_train = build_body_model(body_model_train)
        self.body_model_test = build_body_model(body_model_test)
        self.convention = convention

        self.loss_keypoints2d = build_loss(loss_keypoints2d)
        self.loss_keypoints3d = build_loss(loss_keypoints3d)

        self.loss_smplx_global_orient = build_loss(loss_smplx_global_orient)
        self.loss_smplx_body_pose = build_loss(loss_smplx_body_pose)
        self.loss_smplx_hand_pose = build_loss(loss_smplx_hand_pose)
        self.loss_smplx_jaw_pose = build_loss(loss_smplx_jaw_pose)
        self.loss_smplx_expression = build_loss(loss_smplx_expression)
        self.loss_smplx_betas = build_loss(loss_smplx_betas)
        self.loss_smplx_betas_piror = build_loss(loss_smplx_betas_prior)
        self.loss_adv = build_loss(loss_adv)
        self.loss_camera = build_loss(loss_camera)
        set_requires_grad(self.body_model_train, False)
        set_requires_grad(self.body_model_test, False)

    def train_step(self, data_batch, optimizer, **kwargs):
        """Train step function.

        In this function, the detector will finish the train step following
        the pipeline:
        1. get fake and real SMPLX parameters
        2. optimize discriminator (if have)
        3. optimize generator
        If `self.train_cfg.disc_step > 1`, the train step will contain multiple
        iterations for optimizing discriminator with different input data and
        only one iteration for optimizing generator after `disc_step`
        iterations for discriminator.
        Args:
            data_batch (torch.Tensor): Batch of data as input.
            optimizer (dict[torch.optim.Optimizer]): Dict with optimizers for
                generator and discriminator (if have).
        Returns:
            outputs (dict): Dict with loss, information for logger,
            the number of samples.
        """
        if self.backbone is not None:
            img = data_batch['img']
            features = self.backbone(img)
        else:
            features = data_batch['features']

        if self.neck is not None:
            features = self.neck(features)

        predictions = self.head(features)
        targets = self.prepare_targets(data_batch)

        # optimize discriminator (if have)
        if self.disc is not None:
            self.optimize_discrinimator(predictions, data_batch, optimizer)

        losses = self.compute_losses(predictions, targets)
        # optimizer generator part
        if self.disc is not None:
            adv_loss = self.optimize_generator(predictions)
            losses.update(adv_loss)

        loss, log_vars = self._parse_losses(losses)
        if self.backbone is not None:
            optimizer['backbone'].zero_grad()
        if self.neck is not None:
            optimizer['neck'].zero_grad()
        if self.head is not None:
            optimizer['head'].zero_grad()
        loss.backward()
        if self.backbone is not None:
            optimizer['backbone'].step()
        if self.neck is not None:
            optimizer['neck'].step()
        if self.head is not None:
            optimizer['head'].step()

        outputs = dict(
            loss=loss,
            log_vars=log_vars,
            num_samples=len(next(iter(data_batch.values()))))
        return outputs

    def optimize_discrinimator(self, predictions: dict, data_batch: dict,
                               optimizer: dict):
        """Optimize discrinimator during adversarial training."""
        set_requires_grad(self.disc, True)
        fake_data = self.make_fake_data(predictions, requires_grad=False)
        real_data = self.make_real_data(data_batch)
        fake_score = self.disc(fake_data)
        real_score = self.disc(real_data)

        disc_losses = {}
        disc_losses['real_loss'] = self.loss_adv(
            real_score, target_is_real=True, is_disc=True)
        disc_losses['fake_loss'] = self.loss_adv(
            fake_score, target_is_real=False, is_disc=True)
        loss_disc, log_vars_d = self._parse_losses(disc_losses)

        optimizer['disc'].zero_grad()
        loss_disc.backward()
        optimizer['disc'].step()

    def optimize_generator(self, predictions: dict):
        """Optimize generator during adversarial training."""
        set_requires_grad(self.disc, False)
        fake_data = self.make_fake_data(predictions, requires_grad=True)
        pred_score = self.disc(fake_data)
        loss_adv = self.loss_adv(
            pred_score, target_is_real=True, is_disc=False)
        loss = dict(adv_loss=loss_adv)
        return loss

    def compute_keypoints3d_loss(
            self,
            pred_keypoints3d: torch.Tensor,
            gt_keypoints3d: torch.Tensor,
            has_keypoints3d: Optional[torch.Tensor] = None):
        """Compute loss for 3d keypoints."""
        keypoints3d_conf = gt_keypoints3d[:, :, 3].float().unsqueeze(-1)
        keypoints3d_conf = keypoints3d_conf.repeat(1, 1, 3)
        pred_keypoints3d = pred_keypoints3d.float()
        gt_keypoints3d = gt_keypoints3d[:, :, :3].float()

        if has_keypoints3d is None:
            has_keypoints3d
        if keypoints3d_conf[has_keypoints3d == 1].numel() == 0:
            return torch.Tensor([0]).type_as(gt_keypoints3d)
        # Center the predictions using the pelvis
        target_idxs = has_keypoints3d == 1
        pred_keypoints3d = pred_keypoints3d[target_idxs]
        gt_keypoints3d = gt_keypoints3d[target_idxs]
        pred_pelvis = pred_keypoints3d[:,[1,2],:].mean(dim = 1, keepdim=True)
        pred_keypoints3d = pred_keypoints3d - pred_pelvis
        gt_pelvis = gt_keypoints3d[:,[1,2],:].mean(dim = 1, keepdim=True)
        gt_keypoints3d = gt_keypoints3d - gt_pelvis


        loss = self.loss_keypoints3d(
            pred_keypoints3d, gt_keypoints3d, weight = keypoints3d_conf[target_idxs])
        loss /= gt_keypoints3d.shape[0]
        return loss

    def compute_keypoints2d_loss(
            self,
            pred_keypoints3d: torch.Tensor,
            pred_cam: torch.Tensor,
            gt_keypoints2d: torch.Tensor,
            img_res: Optional[int] = 224,
            focal_length: Optional[int] = 5000,
            has_keypoints2d: Optional[torch.Tensor] = None):
        """Compute loss for 2d keypoints."""
        
        keypoints2d_conf = gt_keypoints2d[:, :, 2].float().unsqueeze(-1)
        keypoints2d_conf = keypoints2d_conf.repeat(1, 1, 2)
        gt_keypoints2d = gt_keypoints2d[:, :, :2].float()
        if keypoints2d_conf[has_keypoints2d == 1].numel() == 0:
            return torch.Tensor([0]).type_as(gt_keypoints2d)

        # Expose use weak_perspective_projection
        pred_keypoints2d = weak_perspective_projection(
            pred_keypoints3d,
            scale = pred_cam[:, 0],
            translation = pred_cam[:,1:3]
            )
        gt_keypoints2d = 2 * gt_keypoints2d / (img_res - 1) - 1
        
        target_idxs = has_keypoints2d == 1
        pred_keypoints2d = pred_keypoints2d[target_idxs]
        gt_keypoints2d = gt_keypoints2d[target_idxs]
        loss = self.loss_keypoints2d(
            pred_keypoints2d, gt_keypoints2d, weight = keypoints2d_conf[target_idxs])
        loss /= gt_keypoints2d.shape[0]
        return loss

    def compute_smplx_body_pose_loss(self, pred_rotmat: torch.Tensor,
                               gt_pose: torch.Tensor, has_smplx_body_pose: torch.Tensor):
        """Compute loss for smplx body pose."""
        num_joints = pred_rotmat.shape[1]
        target_idxs = has_smplx_body_pose == 1
        if gt_pose[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_pose)
        
        gt_rotmat = batch_rodrigues(gt_pose.view(-1, 3)).view(-1, num_joints, 3, 3)

        loss = self.loss_smplx_body_pose(
            pred_rotmat[target_idxs], gt_rotmat[target_idxs])
        return loss

    def compute_smplx_global_orient_loss(self, pred_rotmat: torch.Tensor,
                               gt_global_orient: torch.Tensor, has_smplx_global_orient: torch.Tensor):
        """Compute loss for smplx global orient."""
        target_idxs = has_smplx_global_orient == 1
        if gt_global_orient[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_global_orient)

        gt_rotmat = batch_rodrigues(gt_global_orient.view(-1, 3)).view(-1, 1, 3, 3)

        loss = self.loss_smplx_global_orient(
            pred_rotmat[target_idxs], gt_rotmat[target_idxs])
        return loss
    
    def compute_smplx_jaw_pose_loss(self, pred_rotmat: torch.Tensor,
                               gt_jaw_pose: torch.Tensor, has_smplx_jaw_pose: torch.Tensor, face_conf: torch.Tensor):
        """Compute loss for smplx jaw pose."""
        target_idxs = has_smplx_jaw_pose == 1
        if gt_jaw_pose[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_jaw_pose)

        gt_rotmat = batch_rodrigues(gt_jaw_pose.view(-1, 3)).view(-1, 1, 3, 3)
        conf = face_conf.mean(axis=1).float()
        conf = conf.view(-1, 1, 1, 1)

        loss = self.loss_smplx_jaw_pose(
            pred_rotmat[target_idxs], gt_rotmat[target_idxs], weight = conf[target_idxs])
        return loss

    def compute_smplx_hand_pose_loss(self, pred_rotmat: torch.Tensor,
                               gt_hand_pose: torch.Tensor, has_smplx_hand_pose: torch.Tensor, hand_conf: torch.Tensor):
        """Compute loss for smplx left/right hand pose."""
        joint_num = pred_rotmat.shape[1]
        target_idxs = has_smplx_hand_pose == 1
        if gt_hand_pose[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_hand_pose)
        gt_rotmat = batch_rodrigues(gt_hand_pose.view(-1, 3)).view(-1, joint_num, 3, 3)
        conf = hand_conf.mean(axis=1, keepdim= True).float().expand(-1,joint_num)
        conf = conf.view(-1, joint_num, 1, 1)

        loss = self.loss_smplx_hand_pose(
            pred_rotmat[target_idxs], gt_rotmat[target_idxs], weight = conf[target_idxs])
        return loss


    def compute_smplx_betas_loss(self, pred_betas: torch.Tensor, gt_betas: torch.Tensor,
                                has_smplx_betas: torch.Tensor):
        """Compute loss for smplx betas."""
        target_idxs = has_smplx_betas == 1
        if gt_betas[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_betas)

        loss = self.loss_smplx_betas(
            pred_betas[target_idxs], gt_betas[target_idxs])
        loss = loss / gt_betas[target_idxs].shape[0]
        return loss
    
    def compute_smplx_betas_prior_loss(self, pred_betas: torch.Tensor):
        """Compute prior loss for smplx betas."""
        loss = self.loss_smplx_betas_piror(pred_betas)
        return loss

    def compute_smplx_expression_loss(self, pred_expression: torch.Tensor, gt_expression: torch.Tensor, has_smplx_expression: torch.Tensor, face_conf: torch.Tensor):
        """Compute loss for smplx betas."""
        target_idxs = has_smplx_expression == 1
        if gt_expression[target_idxs].numel() == 0:
            return torch.Tensor([0]).type_as(gt_expression)
        conf = face_conf.mean(axis=1).float()
        conf = conf.view(-1, 1)

        loss = self.loss_smplx_expression(
            pred_expression[target_idxs], gt_expression[target_idxs], weight = conf[target_idxs])
        loss = loss / gt_expression[target_idxs].shape[0]
        return loss

    def compute_camera_loss(self, cameras: torch.Tensor):
        """Compute loss for predicted camera parameters."""
        loss = self.loss_camera(cameras)
        return loss

    def compute_losses(self, predictions: dict, targets: dict):
        """Compute losses."""
        pred_param = predictions['pred_param']
        pred_cam = predictions['pred_cam']
        gt_keypoints3d = targets['keypoints3d']
        gt_keypoints2d = targets['keypoints2d']
        
        if self.body_model_train is not None:
            pred_output = self.body_model_train(**pred_param)
            pred_keypoints3d = pred_output['joints']
            pred_vertices = pred_output['vertices']
        if 'has_keypoints3d' in targets:
            has_keypoints3d = targets['has_keypoints3d'].squeeze(-1)
        else:
            has_keypoints3d = None
        if 'has_keypoints2d' in targets:
            has_keypoints2d = targets['has_keypoints2d'].squeeze(-1)
        else:
            has_keypoints2d = None

        losses = {}
        if self.loss_keypoints3d is not None:
            losses['keypoints3d_loss'] = self.compute_keypoints3d_loss(
                pred_keypoints3d,
                gt_keypoints3d,
                has_keypoints3d=has_keypoints3d)
        if self.loss_keypoints2d is not None:
            losses['keypoints2d_loss'] = self.compute_keypoints2d_loss(
                pred_keypoints3d,
                pred_cam,
                gt_keypoints2d,
                img_res = targets['img'].shape[-1],
                has_keypoints2d=has_keypoints2d)
        if self.loss_smplx_global_orient is not None:
            pred_global_orient = pred_param['global_orient']
            pred_global_orient = pose2rotmat(pred_global_orient)
            gt_global_orient = targets['smplx_global_orient']
            has_smplx_global_orient = targets['has_smplx_global_orient'].squeeze(-1)
            losses['smplx_global_orient'] = self.compute_smplx_global_orient_loss(
                pred_global_orient, gt_global_orient, has_smplx_global_orient
            )
        if self.loss_smplx_body_pose is not None:
            pred_pose = pred_param['body_pose']
            pred_pose = pose2rotmat(pred_pose)
            gt_pose = targets['smplx_body_pose']
            has_smplx_body_pose = targets['has_smplx_body_pose'].squeeze(-1)
            losses['smplx_body_pose_loss'] = self.compute_smplx_body_pose_loss(
                pred_pose, gt_pose, has_smplx_body_pose)
        if self.loss_smplx_jaw_pose is not None:
            pred_jaw_pose = pred_param['jaw_pose']
            pred_jaw_pose = pose2rotmat(pred_jaw_pose)
            gt_jaw_pose = targets['smplx_jaw_pose']
            face_conf = get_keypoint_idxs_by_part('head',self.convention)
            has_smplx_jaw_pose = targets['has_smplx_jaw_pose'].squeeze(-1)
            losses['smplx_jaw_pose_loss'] = self.compute_smplx_jaw_pose_loss(
                pred_jaw_pose, gt_jaw_pose, has_smplx_jaw_pose, gt_keypoints2d[:,face_conf,2]
            )
        if self.loss_smplx_hand_pose is not None:
            pred_right_hand_pose = pred_param['right_hand_pose']
            pred_right_hand_pose = pose2rotmat(pred_right_hand_pose)
            gt_right_hand_pose = targets['smplx_right_hand_pose']
            right_hand_conf = get_keypoint_idxs_by_part('right_hand', self.convention)
            has_smplx_right_hand_pose = targets['has_smplx_right_hand_pose'].squeeze(-1)
            losses['smplx_right_hand_pose_loss'] = self.compute_smplx_hand_pose_loss(
                pred_right_hand_pose, gt_right_hand_pose, has_smplx_right_hand_pose, gt_keypoints2d[:,right_hand_conf,2]
            )
            if 'left_hand_pose' in pred_param:
                pred_left_hand_pose = pred_param['left_hand_pose']
                pred_left_hand_pose = pose2rotmat(pred_left_hand_pose)
                gt_left_hand_pose = targets['smplx_left_hand_pose']
                left_hand_conf = get_keypoint_idxs_by_part('left_hand', self.convention)
                has_smplx_left_hand_pose = targets['has_smplx_left_hand_pose'].squeeze(-1)
                losses['smplx_left_hand_pose_loss'] = self.compute_smplx_hand_pose_loss(
                    pred_left_hand_pose, gt_left_hand_pose, has_smplx_left_hand_pose, gt_keypoints2d[:,left_hand_conf,2]
                )
        if self.loss_smplx_betas is not None:
            pred_betas = pred_param['betas']
            gt_betas = targets['smplx_betas']
            has_smplx_betas = targets['has_smplx_betas'].squeeze(-1)
            losses['smplx_betas_loss'] = self.compute_smplx_betas_loss(
                pred_betas, gt_betas, has_smplx_betas)
        if self.loss_smplx_expression is not None:
            pred_expression = pred_param['expression']
            gt_expression = targets['smplx_expression']
            face_conf = get_keypoint_idxs_by_part('head',self.convention)
            has_smplx_expression = targets['has_smplx_expression'].squeeze(-1)
            losses['smplx_expression_loss'] = self.compute_smplx_expression_loss(
                pred_expression, gt_expression, has_smplx_expression, gt_keypoints2d[:, face_conf, 2])
        if self.loss_smplx_betas_piror is not None:
            pred_betas = pred_param['betas']
            losses['smplx_betas_prior_loss'] = self.compute_smplx_betas_prior_loss(
                pred_betas)
        if self.loss_camera is not None:
            losses['camera_loss'] = self.compute_camera_loss(pred_cam)
        return losses

    @abstractmethod
    def make_fake_data(self, predictions, requires_grad):
        pass

    @abstractmethod
    def make_real_data(self, data_batch):
        pass

    @abstractmethod
    def prepare_targets(self, data_batch):
        pass

    def forward_train(self, **kwargs):
        """Forward function for general training.

        For mesh estimation, we do not use this interface.
        """
        raise NotImplementedError('This interface should not be used in '
                                  'current training schedule. Please use '
                                  '`train_step` for training.')

    @abstractmethod
    def forward_test(self, img, img_metas, **kwargs):
        """Defines the computation performed at every call when testing."""
        pass


class SMPLXImageBodyModelEstimator(SMPLXBodyModelEstimator):

    def prepare_targets(self, data_batch: dict):
        # Image Mesh Estimator does not need extra process for ground truth
        return data_batch

    def make_fake_data(self, predictions, requires_grad):
        return super().make_fake_data(predictions, requires_grad)

    def make_real_data(self, data_batch):
        return super().make_real_data(data_batch)

    def forward_test(self, img: torch.Tensor, img_metas: dict, **kwargs):
        """Defines the computation performed at every call when testing."""
        if self.backbone is not None:
            features = self.backbone(img)
        else:
            features = kwargs['features']

        if self.neck is not None:
            features = self.neck(features)

        predictions = self.head(features)
        pred_param = predictions['pred_param']
        pred_cam = predictions['pred_cam']

        pred_output = self.body_model_test(**pred_param)

        pred_vertices = pred_output['vertices']
        pred_keypoints_3d = pred_output['joints']
        all_preds = {}
        all_preds['keypoints_3d'] = pred_keypoints_3d.detach().cpu().numpy()
        for value in pred_param.values():
            if isinstance(value, torch.Tensor):
                value = value.detach().cpu().numpy()
        all_preds['param'] = pred_param
        all_preds['camera'] = pred_cam.detach().cpu().numpy()
        all_preds['vertices'] = pred_vertices.detach().cpu().numpy()
        image_path = []
        for img_meta in img_metas:
            image_path.append(img_meta['image_path'])
        all_preds['image_path'] = image_path
        all_preds['image_idx'] = kwargs['sample_idx']
        return all_preds

