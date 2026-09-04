_base_root = '{{ fileDirname }}'
crop_size = (
    512,
    512,
)
cudnn_benchmark = True
data_preprocessor = dict(
    bgr_to_rgb=False,
    mean=[
        42.72,
        46.32,
        40.33,
    ],
    pad_val=0,
    seg_pad_val=255,
    size=(
        512,
        512,
    ),
    std=[
        47.11,
        47.57,
        48.98,
    ],
    test_cfg=dict(size_divisor=128),
    type='SegDataPreProcessor')
data_root = '/home/featurize/data/yunnan_dataset'
dataset_type = 'landsDataset'
default_hooks = dict(
    checkpoint=dict(
        by_epoch=True,
        interval=1,
        max_keep_ckpts=3,
        save_best='mIoU',
        type='CheckpointHook'),
    logger=dict(interval=100, log_metric_by_epoch=True, type='LoggerHook'),
    param_scheduler=dict(type='ParamSchedulerHook'),
    sampler_seed=dict(type='DistSamplerSeedHook'),
    timer=dict(type='IterTimerHook'),
    visualization=dict(type='SegVisualizationHook'))
default_scope = 'mmseg'
env_cfg = dict(
    cudnn_benchmark=True,
    dist_cfg=dict(backend='nccl'),
    mp_cfg=dict(mp_start_method='fork', opencv_num_threads=0))
img_norm_cfg = dict(
    mean=[
        42.72,
        46.32,
        40.33,
    ], std=[
        47.11,
        47.57,
        48.98,
    ], to_rgb=False)
img_ratios = [
    0.5,
    0.75,
    1.0,
    1.25,
    1.5,
    1.75,
]
img_scale = (
    512,
    512,
)
launcher = 'none'
load_from = None
log_level = 'INFO'
log_processor = dict(by_epoch=True)
model = dict(
    auxiliary_head=dict(
        align_corners=False,
        channels=256,
        concat_input=False,
        dropout_ratio=0.1,
        in_channels=512,
        in_index=2,
        loss_decode=[
            dict(
                class_weight=[
                    1,
                    1,
                    1,
                    100,
                    1,
                    1000,
                ],
                loss_weight=1.0,
                per_image=False,
                reduction='none',
                type='LovaszLoss'),
        ],
        norm_cfg=dict(num_groups=32, requires_grad=True, type='GN'),
        num_classes=6,
        num_convs=1,
        out_channels=6,
        type='FCNHead'),
    backbone=dict(
        dinov3_cfg=dict(
            adapt_patch_size='center_padding',
            frozen_stages=-1,
            img_size=512,
            model_name='dinov3_vitl16',
            output_cls_token=False,
            repo_dir=f'{_base_root}/dinov3_swinV1/dinov3',
            weights=f'{_base_root}/dinov3_swinV1/dinov3_vitl16_pretrain_sat493m-eadcf0ff.pth'
        ),
        dinov3_out_channels=[
            1024,
            1024,
            1024,
            1024,
        ],
        feature_adapt_cfg=dict(
            act_cfg=dict(type='GELU'),
            adapt_strides=[
                4,
                2,
                1,
                1,
            ],
            conv_type='Conv2d',
            norm_cfg=dict(num_groups=32, requires_grad=True, type='GN')),
        fusion_cfg=dict(
            act_cfg=dict(type='ReLU'),
            attn_channels=384,
            fusion_order='low2high',
            in_channels_list=[
                128,
                256,
                512,
                1024,
            ],
            intra_stage_type='attention',
            norm_cfg=dict(num_groups=32, requires_grad=True, type='GN'),
            type='cross_stage_progressive'),
        swin_cfg=dict(
            act_cfg=dict(type='GELU'),
            attn_drop_rate=0.0,
            depths=[
                2,
                2,
                18,
                2,
            ],
            drop_path_rate=0.3,
            drop_rate=0.0,
            embed_dims=128,
            init_cfg=dict(
                checkpoint=f'{_base_root}/dinov3_swinV1/swin_base_patch4_window7_224_20220317-e9b98025.pth',
                type='Pretrained'),
            mlp_ratio=4,
            norm_cfg=dict(type='LN'),
            num_heads=[
                4,
                8,
                16,
                32,
            ],
            out_indices=(
                0,
                1,
                2,
                3,
            ),
            patch_norm=True,
            patch_size=4,
            qk_scale=None,
            qkv_bias=True,
            use_abs_pos_embed=False,
            window_size=7),
        type='DINOv3SwinEncoder'),
    data_preprocessor=dict(
        bgr_to_rgb=False,
        mean=[
            42.72,
            46.32,
            40.33,
        ],
        pad_val=0,
        seg_pad_val=255,
        size=(
            512,
            512,
        ),
        std=[
            47.11,
            47.57,
            48.98,
        ],
        test_cfg=dict(size_divisor=128),
        type='SegDataPreProcessor'),
    decode_head=dict(
        align_corners=False,
        channels=512,
        dropout_ratio=0.1,
        in_channels=[
            128,
            256,
            512,
            1024,
        ],
        in_index=[
            0,
            1,
            2,
            3,
        ],
        loss_decode=[
            dict(
                class_weight=[
                    1,
                    1,
                    1,
                    10,
                    1,
                    1000,
                ],
                loss_weight=1.0,
                per_image=False,
                reduction='none',
                type='LovaszLoss'),
        ],
        norm_cfg=dict(num_groups=32, requires_grad=True, type='GN'),
        num_classes=6,
        out_channels=6,
        pool_scales=(
            1,
            2,
            3,
            6,
        ),
        type='UPerHead'),
    pretrained=None,
    test_cfg=dict(mode='whole'),
    train_cfg=dict(),
    type='EncoderDecoder')
norm_cfg = dict(num_groups=32, requires_grad=True, type='GN')
optim_wrapper = dict(
    clip_grad=dict(max_norm=0.01, norm_type=2),
    optimizer=dict(
        betas=(
            0.9,
            0.999,
        ),
        eps=1e-08,
        lr=0.008,
        type='AdamW',
        weight_decay=0.05),
    paramwise_cfg=dict(
        custom_keys=dict({
            'backbone':
            dict(decay_mult=1.0, lr_mult=1.0),
            'backbone.feature_adapt_layers':
            dict(decay_mult=1.0, lr_mult=1.0),
            'norm':
            dict(decay_mult=0.0)
        }),
        norm_decay_mult=0.0),
    type='OptimWrapper')
param_scheduler = [
    dict(
        begin=0,
        by_epoch=True,
        convert_to_iter_based=True,
        end=5,
        start_factor=0.001,
        type='LinearLR'),
    dict(
        T_max=25,
        begin=5,
        by_epoch=True,
        convert_to_iter_based=True,
        end=30,
        eta_min=8e-06,
        type='CosineAnnealingLR'),
]
randomness = dict(seed=0)

test_cfg = dict(type='TestLoop')
test_dataloader = dict(
    batch_size=2,
    dataset=dict(
        data_prefix=dict(img_path='img_dir/val', seg_map_path='ann_dir/val'),
        data_root='/home/featurize/data/yunnan_dataset',
        pipeline=[
            dict(type='LoadSingleRSImageFromFile'),
            dict(keep_ratio=True, scale=(
                512,
                512,
            ), type='Resize'),
            dict(reduce_zero_label=False, type='LoadAnnotations'),
            dict(type='PackSegInputs'),
        ],
        type='landsDataset'),
    num_workers=4,
    persistent_workers=True,
    sampler=dict(shuffle=False, type='DefaultSampler'))
test_evaluator = dict(
    iou_metrics=[
        'mIoU',
        'mDice',
        'mFscore',
    ], type='IoUMetric')
train_cfg = dict(max_epochs=100, type='EpochBasedTrainLoop', val_interval=1)
train_dataloader = dict(
    batch_size=2,
    dataset=dict(
        data_prefix=dict(
            img_path='img_dir/train', seg_map_path='ann_dir/train'),
        data_root='/home/featurize/data/yunnan_dataset',
        pipeline=[
            dict(type='LoadSingleRSImageFromFile'),
            dict(reduce_zero_label=False, type='LoadAnnotations'),
            dict(type='PackSegInputs'),
        ],
        type='landsDataset'),
    num_workers=4,
    persistent_workers=True,
    sampler=dict(shuffle=True, type='DefaultSampler'))
tta_model = dict(type='SegTTAModel')
tta_pipeline = [
    dict(backend_args=None, type='LoadSingleRSImageFromFile'),
    dict(
        transforms=[
            [
                dict(keep_ratio=True, scale_factor=1.0, type='Resize'),
            ],
            [
                dict(direction='horizontal', prob=0.0, type='RandomFlip'),
                dict(direction='horizontal', prob=1.0, type='RandomFlip'),
            ],
            [
                dict(type='LoadAnnotations'),
            ],
            [
                dict(type='PackSegInputs'),
            ],
        ],
        type='TestTimeAug'),
]
val_cfg = dict(type='ValLoop')
val_dataloader = dict(
    batch_size=2,
    dataset=dict(
        data_prefix=dict(img_path='img_dir/val', seg_map_path='ann_dir/val'),
        data_root='/home/featurize/data/yunnan_dataset',
        pipeline=[
            dict(type='LoadSingleRSImageFromFile'),
            dict(keep_ratio=True, scale=(
                512,
                512,
            ), type='Resize'),
            dict(reduce_zero_label=False, type='LoadAnnotations'),
            dict(type='PackSegInputs'),
        ],
        type='landsDataset'),
    num_workers=4,
    persistent_workers=True,
    sampler=dict(shuffle=False, type='DefaultSampler'))
val_evaluator = dict(
    iou_metrics=[
        'mIoU',
        'mDice',
        'mFscore',
    ], type='IoUMetric')
vis_backends = [
    dict(type='LocalVisBackend'),
]
visualizer = dict(
    name='visualizer',
    type='SegLocalVisualizer',
    vis_backends=[
        dict(type='LocalVisBackend'),
    ])
work_dir = '/home/featurize/work/mmsegmentation_25717_mine/mmsegmentation/work_dirs/dinov3_swin_upernet_swinmainE'
test_pipeline = [dict(type='LoadSingleRSImageFromFile'), dict(keep_ratio=True, scale=(512, 512), type='Resize'), dict(type='PackSegInputs')]
