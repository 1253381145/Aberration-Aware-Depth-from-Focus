""" Forward and backward Monte-Carlo integral functions.
"""
import torch
import numpy as np
import torch.nn.functional as nnF

from .basics import EPSILON

def forward_integral(ray, ps, ks, pointc_ref=None, interpolate=False):
    """ Forward integral model, including PSF and vignetting

    Args:
        ray: Ray object. Shape of ray.o is [spp, N, 3].
        ps: pixel size
        ks: kernel size.
        pointc_ref: reference pointc, shape [2]
        center: whether to center the PSF.
        interpolate: whether to interpolate the PSF

    Returns:
        psf: point spread function, shape [N, ks, ks]
    """
    single_point = True if len(ray.o.shape) == 2 else False
    points = - ray.o[..., :2]       # shape [spp, N, 2] or [spp, 2]. flip points.
    psf_range = [(- ks / 2 + 0.5) * ps, (ks / 2 - 0.5) * ps]    # this ensures the pixel size doesnot change in assign_points_to_pixels function
    
    # ==> PSF center
    if pointc_ref is None:
        # Use RMS center
        pointc = (points * ray.ra.unsqueeze(-1)).sum(0) / ray.ra.unsqueeze(-1).sum(0).add(EPSILON)
        points_shift = points - pointc
    else:
        # Use manually given center (can be calculated by chief ray or perspective)
        points_shift = points - pointc_ref.to(points.device)
    
    # ==> Remove invalid points
    ra = ray.ra * (points_shift[...,0].abs() < (psf_range[1] - 0.01*ps)) * (points_shift[...,1].abs() < (psf_range[1] - 0.01*ps))   # shape [spp, N] or [spp].
    points_shift *= ra.unsqueeze(-1)
    
    # ==> Calculate PSF
    if single_point:
        obliq = ray.d[:, 2]**2
        psf = assign_points_to_pixels(points=points_shift, ks=ks, x_range=psf_range, y_range=psf_range, ra=ra, obliq=obliq)

    else:
        psf = []
        for i in range(ray.o.shape[1]):
            points_shift0 = points_shift[:, i, :]   # from [spp, N, 2] to [spp, 2]
            ra0 = ra[:, i]                          # from [spp, N] to [spp]

            obliq = ray.d[:, i, 2]**2
            psf0 = assign_points_to_pixels(points=points_shift0, ks=ks, x_range=psf_range, y_range=psf_range, ra=ra0, obliq=obliq)
            psf.append(psf0)

        psf = torch.stack(psf, dim=0)   # shape [N, ks, ks]
    
    return psf


def assign_points_to_pixels(points, ks, x_range, y_range, ra, interpolate=True, coherent=False, phase=None, d=None, obliq=None, wvln=0.589):
    """ Assign points to pixels, both coherent and incoherent. Use advanced indexing to increment the count for each corresponding pixel. This function can only compute single point source, single wvln. If you want to compute multiple point or muyltiple wvln, please call this function multiple times.
    
    Args:
        points: shape [spp, 1, 2]
        ks: kernel size
        x_range: [x_min, x_max]
        y_range: [y_min, y_max]
        ra: shape [spp, 1, 1]
        interpolate: whether to interpolate
        coherent: whether to consider coherence
        phase: shape [spp, 1, 1]

    Returns:
        psf: shape [ks, ks]
    """
    # ==> Parameters
    device = points.device
    x_min, x_max = x_range
    y_min, y_max = y_range
    ps = (x_max - x_min) / (ks - 1)

    # ==> Normalize points to the range [0, 1]
    points_normalized = torch.zeros_like(points)
    points_normalized[:, 0] = (points[:, 1] - y_max) / (y_min - y_max)
    points_normalized[:, 1] = (points[:, 0] - x_min) / (x_max - x_min)

    if interpolate:
        # ==> Weight. The trick here is to use (ks - 1) to compute normalized indices
        pixel_indices_float = points_normalized * (ks - 1)
        w_b = pixel_indices_float[..., 0] - pixel_indices_float[..., 0].floor()
        w_r = pixel_indices_float[..., 1] - pixel_indices_float[..., 1].floor()

        # ==> Pixel indices
        pixel_indices_tl = pixel_indices_float.floor().long()
        pixel_indices_tr = torch.stack((pixel_indices_float[:, 0], pixel_indices_float[:, 1]+1), dim=-1).floor().long()
        pixel_indices_bl = torch.stack((pixel_indices_float[:, 0]+1, pixel_indices_float[:, 1]), dim=-1).floor().long()
        pixel_indices_br = pixel_indices_tl + 1

        if coherent:
            # ==> Use advanced indexing to increment the count for each corresponding pixel
            grid = torch.zeros(ks, ks).to(device) + 0j
            grid.index_put_(tuple(pixel_indices_tl.t()), (1-w_b)*(1-w_r)*ra*torch.exp(1j*phase), accumulate=True)
            grid.index_put_(tuple(pixel_indices_tr.t()), (1-w_b)*w_r*ra*torch.exp(1j*phase), accumulate=True)
            grid.index_put_(tuple(pixel_indices_bl.t()), w_b*(1-w_r)*ra*torch.exp(1j*phase), accumulate=True)
            grid.index_put_(tuple(pixel_indices_br.t()), w_b*w_r*ra*torch.exp(1j*phase), accumulate=True)

        else:
            grid = torch.zeros(ks, ks).to(points.device)
            grid.index_put_(tuple(pixel_indices_tl.t()), (1-w_b)*(1-w_r)*ra, accumulate=True)
            grid.index_put_(tuple(pixel_indices_tr.t()), (1-w_b)*w_r*ra, accumulate=True)
            grid.index_put_(tuple(pixel_indices_bl.t()), w_b*(1-w_r)*ra, accumulate=True)
            grid.index_put_(tuple(pixel_indices_br.t()), w_b*w_r*ra, accumulate=True)

    else:
        pixel_indices_float = points_normalized * (ks - 1)
        pixel_indices_tl = pixel_indices_float.floor().long()

        grid = torch.zeros(ks, ks).to(points.device)
        grid.index_put_(tuple(pixel_indices_tl.t()), ra, accumulate=True)
        
    return grid


