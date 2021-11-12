## Cameras

### Camera types
We follow `pytorch3d` cameras. The extrinsic matrix is defined in `view_to_world` and `right matrix multiplication`, and intrinsic matrix is defined as `left matrix multiplication`.
In mmhuman3d, the recommended way to initialize a camera is by passing `K`, `R`, `T` matrix directly.
You can slice the cameras by index.
```python
K.shape: (100, 4, 4)
R.shape: (100, 3, 3)
T.shape (100, 3)
# K, R, T should have the same length.
cameras = PerspectiveCameras(K=K, R=R, T=T)
len(cameras): 100
len(cameras[:10]): 10
```
- **Build cameras:**
    Take the usually used `PerspectiveCameras` and `WeakPerspectiveCameras` as examples. If `K`, `R`, `T` are not specified, the K will use default K by `compute_default_projection_matrix` and `R` will be identical matrix, `T` will be zeros. You can also specify by overwriting the parameters for `compute_default_projection_matrix`.
    ```python
    from mmhuman3d.core.cameras import build_cameras
    cam = build_cameras(dict(type='PerspectiveCameras', in_ndc=True, image_size=(1000, 1000), device=device))
    cam = build_cameras(dict(type='PerspectiveCameras', K=K, R=R, T=T, in_ndc=True, image_size=(1000, 1000), device=device))
    cam = build_cameras(dict(type='PerspectiveCameras', principal_point=(500, 500), focal_length=(1000, 1000),in_ndc=False, image_size=(1000, 1000), device=device))
    cam = build_cameras(dict(type='WeakPerspectiveCameras', K=K, R=R, T=T, image_size=(1000, 1000), device=device))
    cam = build_cameras(dict(type='WeakPerspectiveCameras', pred_cam=pred_cam, image_size=(1000, 1000), device=device))
    ```
- **Perspective:**
    format of intrinsic matrix:
    fx, fy is focal_length, px, py is principal_point.
    ```python
    K = [
            [fx,   0,   px,   0],
            [0,   fy,   py,   0],
            [0,    0,    0,   1],
            [0,    0,    1,   0],
        ]
    ```
    Detailed infomation refer to [pytorch3d](https://github.com/facebookresearch/pytorch3d/blob/main/pytorch3d/renderer/cameras.py#L895).

- **WeakPerspective:**
    format of intrinsic matrix:
    ```python
    K = [
            [sx*r,   0,    0,   tx*sx*r],
            [0,     sy,    0,   ty*sy],
            [0,     0,     1,       0],
            [0,     0,     0,       1],
        ]
    ```
    `WeakPerspectiveCameras` is orthographics indeed, mainly for SMPL(x) projection.
    Detailed infomation refer to [mmhuman3d](mmhuman3d/core/cameras/cameras.py#L40).
    This can be converted from SMPL predicted camera parameter by:
    ```python
    from mmhuman3d.core.cameras import WeakPerspectiveCameras
    K = WeakPerspectiveCameras.init_from_pred_cam(pred_cam)
    ```
    The pred_cam is array/tensor of shape (frame, 4) consists of [scale_x, scale_y, transl_x, transl_y]. See in [Vibe](https://github.com/mkocabas/VIBE/blob/master/lib/utils/renderer.py#L40-L47).

- **FoVPerspective:**
    ```python
    format of intrinsic matrix:
    K = [
            [s1,   0,   w1,   0],
            [0,   s2,   h1,   0],
            [0,    0,   f1,  f2],
            [0,    0,    1,   0],
        ]
    ```
    s1, s2, w1, h1, f1, f2 is related to FoV parameters, detailed infomation refer to [pytorch3d](https://github.com/facebookresearch/pytorch3d/blob/main/pytorch3d/renderer/cameras.py).

- **Orthographics:**
    format of intrinsic matrix:
    ```python
    K = [
            [fx,   0,    0,  px],
            [0,   fy,    0,  py],
            [0,    0,    1,   0],
            [0,    0,    0,   1],
    ]
    ```
    Detailed infomation refer to [pytorch3d](https://github.com/facebookresearch/pytorch3d/blob/main/pytorch3d/renderer/cameras.py).

- **FoVOrthographics:**
    ```python
    K = [
            [scale_x,        0,         0,  -mid_x],
            [0,        scale_y,         0,  -mix_y],
            [0,              0,  -scale_z,  -mid_z],
            [0,              0,         0,       1],
    ]
    ```
    scale_x, scale_y, scale_z, mid_x, mid_y, mid_z is related to FoV parameters, related infomation refer to [pytorch3d](https://github.com/facebookresearch/pytorch3d/blob/main/pytorch3d/renderer/cameras.py).

### Camera conventions
- **Convert between different cameras:**
    We name intrinsic matrix as `K`, rotation matrix as `R` and translation matrix as `T`.
    Different camera conventions have different axis directions, and some defined as left matrix multiplication and some as right. Intrinsic and extrinsic matrix should be of the same multiplication convention, but some conventions like `pytorch3d` uses right matrix multiplication in computation procedure but passes left matrix multiplication `K` when initializing the cameras(mainly for better understanding).
    Conversion between `NDC` and `screen` also influence the intrinsic matrix, this is independent to camera conventions but should also be included.
    If you want to use an existing convention, choose in `['opengl', 'opencv', 'pytorch3d', 'pyrender', 'open3d']`.
    E.g., you want to convert your opencv calibration camera to pytorch3d NDC defined camera for rendering, you can do:
    ```python
    from mmhuman3d.core.conventions.cameras import convert_cameras
    import torch
    K, R, T = convert_cameras(K=K, R=R, T=T, in_ndc_src=False, in_ndc_dst=True, resolution_src=(1080, 1920), convention_src='opencv', convention_dst='pytorch3d')
    ```
    Input K could be None, or `array`/`tensor` of shape (batch_size, 3, 3) or (batch_size, 4, 4).
    Input R could be None, or `array`/`tensor` of shape (batch_size, 3, 3).
    Input T could be None, or `array`/`tensor` of shape (batch_size, 3).
    If the original `K` is `None`, it will remain `None`. If the original `R` is `None`, it will be set as identity matrix. If the original `T` is `None`, it will be set as zeros.
    If you do not know about `NDC` defined camera and `screen` defined camera, please refer to [pytorch3d](https://github.com/facebookresearch/pytorch3d/blob/main/docs/notes/cameras.md).

- **Define your new camera convention:**

    If want to use a new convention, define your convention in [CAMERA_CONVENTION_FACTORY](mmhuman3d/core/conventions/cameras/__init__.py) by the order of right to, up to, and off screen. E.g., the first one is pyrender and its convention should be '+x+y+z'. '+' could be ignored. The second one is opencv and its convention should be '+x-y-z'. The third one is pytorch3d and its convention should be '-xyz'.
    ```
    opengl(pyrender)       opencv             pytorch3d
        y                   z                     y
        |                  /                      |
        |                 /                       |
        |_______x        /________x     x________ |
        /                |                        /
       /                 |                       /
    z /                y |                    z /
    ```

### Some convert functions
Convert functions are also defined in conventions.cameras.
- **NDC & screen:**

    ```python
    from mmhuman3d.core.conventions.cameras import convert_ndc_to_screen, convert_screen_to_ndc
    K = convert_ndc_to_screen(K, resolution=(1080, 1920), is_perspective=True)
    K = convert_screen_to_ndc(K, resolution=(1080, 1920), is_perspective=True)
    ```

- **3x3 & 4x4 intrinsic matrix**

    ```python
    from mmhuman3d.core.conventions.cameras import convert_K_3x3_to_4x4, convert_K_4x4_to_3x3
    K = convert_K_3x3_to_4x4(K, is_perspective=True)
    K = convert_K_4x4_to_3x3(K, is_perspective=True)
    ```

-**world & view:**
    Convert between world & view coordinates.

    ```python
    from mmhuman3d.core.conventions.cameras import convert_world_view
    R, T = convert_world_view(R, T)
    ```
-**weakperspective & perspective:**
    Convert between weakperspective & perspective. zmean is needed.
    WeakperspectiveCameras is in_ndc, so you should pass resolution if perspective not in ndc.

    ```python
    from mmhuman3d.core.conventions.cameras import convert_perspective_to_weakperspective, convert_weakperspective_to_perspective
    K = convert_perspective_to_weakperspective(K, zmean, in_ndc=False, resolution, convention='opencv')
    K = convert_weakperspective_to_perspective(K, zmean, in_ndc=False, resolution, convention='pytorch3d')
    ```

### Compute points
- **Project 3D coordinates to screen:**
    ```python
    points_xydepth = cameras.transform_points_screen(points)
    points_xy = points_xydepth[..., :2]
    ```
- **Compute depth of points:**
    This is simply convert points to the view coordinates and get the z value as depth. Example culd be found in [DepthRenderer](mmhuman3d/core/visualization/renderer/torch3d_renderer/depth_renderer.py).
    ```python
    points_depth = cameras.compute_depth_of_points(points)
    ```