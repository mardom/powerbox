"""Tools for dealing with structured boxes, such as those output by :mod:`powerbox`.

Tools include those for averaging a field angularly, and generating the isotropic
power spectrum.
"""

from __future__ import annotations

import numpy as np
import warnings
from scipy.interpolate import RegularGridInterpolator
from scipy.special import gamma

from . import dft


def _getbins(bins, coords, log):
    try:
        # Fails if coords is not a cube / inhomogeneous.
        max_radius = np.min([np.max(coords, axis=i) for i in range(coords.ndim)])
    except ValueError:
        maxs = [np.max(coords, axis=i) for i in range(coords.ndim)]
        maxs_flat = []
        [maxs_flat.extend(m.ravel()) for m in maxs]
        max_radius = np.min(maxs_flat)
    if not np.iterable(bins):
        if not log:
            bins = np.linspace(coords.min(), max_radius, bins + 1)
        else:
            mn = coords[coords > 0].min()
            bins = np.logspace(np.log10(mn), np.log10(max_radius), bins + 1)
    return bins


def angular_average(
    field,
    coords,
    bins,
    weights=1,
    average=True,
    bin_ave=True,
    get_variance=False,
    log_bins=False,
    interpolation_method=None,
    interp_points_generator=None,
    return_sumweights=False,
):
    r"""
    Average a given field within radial bins.

    This function can be used in fields of arbitrary dimension (memory permitting), and the field need not be centred
    at the origin. The averaging assumes that the grid cells fall completely into the bin which encompasses the
    co-ordinate point for the cell (i.e. there is no weighted splitting of cells if they intersect a bin edge).

    It is optimized for applying a set of weights, and obtaining the variance of the mean, at the same time as
    averaging.

    Parameters
    ----------
    field: nd-array
        An array of arbitrary dimension specifying the field to be angularly averaged.

    coords: nd-array or list of n arrays.
        Either the *magnitude* of the co-ordinates at each point of `field`, or a list of 1D arrays specifying the
        co-ordinates in each dimension.

    bins: float or array.
        The ``bins`` argument provided to histogram. Can be an int or array specifying radial bin edges.

    weights: array, optional
        An array of the same shape as `field`, giving a weight for each entry.

    average: bool, optional
        Whether to take the (weighted) average. If False, returns the (unweighted) sum.

    bin_ave : bool, optional
        Whether to return the bin co-ordinates as the (weighted) average of cells within the bin (if True), or
        the regularly spaced edges of the bins.

    get_variance : bool, optional
        Whether to also return an estimate of the variance of the power in each bin.

    log_bins : bool, optional
        Whether to create bins in log-space.

    interpolation_method : str, optional
        If None, does not interpolate. Currently only 'linear' is supported.

    interp_points_generator : callable, optional
        A function that generates the sample points for the interpolation.
        If None, defaults regular_angular_generator(resolution = 0.1).
        If callable, a function that takes as input an angular resolution for the sampling
        and returns a 1D array of radii and 2D array of angles
        (see documentation on the inputs of _sphere2cartesian for more details on the outputs).
        This function can be used to obtain an angular average over a certain region of the field by
        limiting where the samples are taken for the interpolation. See function above_mu_min_generator
        for an example of such a function.

    return_sumweights : bool, optional
        Whether to return the number of modes in each bin.
        Note that for the linear interpolation case,
        this corresponds to the number of samples averaged over
        (which can be adjusted by supplying a different interp_points_generator
        function with a different angular resolution).

    Returns
    -------
    field_1d : 1D-array
        The angularly-averaged field.

    bins : 1D-array
        Array of same shape as field_1d specifying the radial co-ordinates of the bins. Either the mean co-ordinate
        from the input data, or the regularly spaced bins, dependent on `bin_ave`.

    var : 1D-array, optional
        The variance of the averaged field (same shape as bins), estimated from the mean standard error.
        Only returned if `get_variance` is True.

    Notes
    -----
    If desired, the variance is calculated as the weight unbiased variance, using the formula at
    https://en.wikipedia.org/wiki/Weighted_arithmetic_mean#Reliability_weights for the variance in each cell, and
    normalising by a factor of :math:`V_2/V_1^2` to estimate the variance of the average.

    Examples
    --------
    Create a 3D radial function, and average over radial bins:

    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> x = np.linspace(-5,5,128)   # Setup a grid
    >>> X,Y,Z = np.meshgrid(x,x,x)
    >>> r = np.sqrt(X**2+Y**2+Z**2) # Get the radial co-ordinate of grid
    >>> field = np.exp(-r**2)       # Generate a radial field
    >>> avgfunc, bins = angular_average(field,r,bins=100)   # Call angular_average
    >>> plt.plot(bins, np.exp(-bins**2), label="Input Function")   # Plot input function versus ang. avg.
    >>> plt.plot(bins, avgfunc, label="Averaged Function")

    See Also
    --------
    angular_average_nd : Perform an angular average in a subset of the total dimensions.

    """
    if interpolation_method is not None and interpolation_method != "linear":
        raise ValueError("Only linear interpolation is supported.")
    if len(coords) == len(field.shape):
        # coords are a segmented list of dimensional co-ordinates
        coords_grid = _magnitude_grid(coords)
    elif interpolation_method is not None:
        raise ValueError(
            "Must supply a list of len(field.shape) of 1D coordinate arrays for coords when interpolating!"
        )
    else:
        # coords are the magnitude of the co-ordinates
        # since we are not interpolating, then we can just use the magnitude of the co-ordinates
        coords_grid = coords
    if interpolation_method is None:
        indx, bins, sumweights = _get_binweights(
            coords_grid, weights, bins, average, bin_ave=bin_ave, log_bins=log_bins
        )

        if np.any(sumweights == 0):
            warnings.warn(
                "One or more radial bins had no cells within it.", stacklevel=2
            )
        res = _field_average(indx, field, weights, sumweights)
    else:
        bins = _getbins(bins, coords_grid, log_bins)
        if log_bins:
            bins = np.exp((np.log(bins[1:]) + np.log(bins[:-1])) / 2)
        else:
            bins = (bins[1:] + bins[:-1]) / 2

        sample_coords, r_n = _sample_coords_interpolate(
            coords, bins, weights, interp_points_generator
        )
        res, sumweights = _field_average_interpolate(
            coords, field, bins, weights, sample_coords, r_n
        )
    if get_variance:
        if interpolation_method is None:
            var = _field_variance(indx, field, res, weights, sumweights)
        else:
            raise NotImplementedError(
                "Variance calculation not implemented for interpolation"
            )
        if return_sumweights:
            return res, bins, var, sumweights
        else:
            return res, bins, var
    else:
        if return_sumweights:
            return res, bins, sumweights
        else:
            return res, bins


def _magnitude_grid(x, dim=None):
    if dim is not None:
        return np.sqrt(np.sum(np.meshgrid(*([x**2] * dim), indexing="ij"), axis=0))
    else:
        return np.sqrt(np.sum(np.meshgrid(*([X**2 for X in x]), indexing="ij"), axis=0))


def _get_binweights(coords, weights, bins, average=True, bin_ave=True, log_bins=False):
    # Get a vector of bin edges
    bins = _getbins(bins, coords, log_bins)

    indx = np.digitize(coords.flatten(), bins)

    if average or bin_ave:
        if not np.isscalar(weights):
            if coords.shape != weights.shape:
                raise ValueError(
                    "coords and weights must have the same shape!",
                    coords.shape,
                    weights.shape,
                )
            sumweights = np.bincount(
                indx, weights=weights.flatten(), minlength=len(bins) + 1
            )[1:-1]
        else:
            sumweights = np.bincount(indx, minlength=len(bins) + 1)[1:-1]

        if average:
            binweight = sumweights
        else:
            binweight = 1 * sumweights
            sumweights = np.ones_like(binweight)

        if bin_ave:
            bins = (
                np.bincount(
                    indx, weights=(weights * coords).flatten(), minlength=len(bins) + 1
                )[1:-1]
                / binweight
            )

    else:
        sumweights = np.ones(len(bins) - 1)

    return indx, bins, sumweights


def _spherical2cartesian(r, phi_n):
    r"""Convert spherical coordinates to Cartesian coordinates.

    Parameters
    ----------
    r_n : array-like
        1D array of radii.
    phi_n : array-like
        2D array of azimuthal angles with shape (ndim-1, N), where N is the number of points.
        phi_n[0,:] :math:`\in [0,2*\pi]`, and phi_n[1:,:] :math:`\in [0,\pi]`.

    Returns
    -------
    coords : array-like
        2D array of Cartesian coordinates with shape (ndim, N).

    For more details, see https://en.wikipedia.org/wiki/N-sphere#Spherical_coordinates

    """
    if phi_n.shape[0] == 1:
        return r * np.array([np.cos(phi_n[0]), np.sin(phi_n[0])])
    elif phi_n.shape[0] == 2:
        return r * np.array(
            [
                np.cos(phi_n[0]),
                np.sin(phi_n[0]) * np.cos(phi_n[1]),
                np.sin(phi_n[0]) * np.sin(phi_n[1]),
            ]
        )
    else:
        phi_n = np.concatenate(
            [2 * np.pi * np.ones(phi_n.shape[1])[np.newaxis, ...], phi_n], axis=0
        )
        sines = np.sin(phi_n)
        sines[0, :] = 1
        cum_sines = np.cumprod(sines, axis=0)
        cosines = np.roll(np.cos(phi_n), -1, axis=0)
        return cum_sines * cosines * r


def above_mu_min_angular_generator(bins, dims2avg, angular_resolution=0.1, mu=0.97):
    r"""
    Returns a set of spherical coordinates above a certain :math:`\\mu` value.

    Parameters
    ----------
    bins : array-like
        1D array of radii at which we want to spherically average the field.
    dims2avg : int
        The number of dimensions to average over.
    angular_resolution : float, optional
        The angular resolution in radians for the sample points for the interpolation.
    mu : float, optional
        The minimum value of :math:`\\cos(\theta), \theta = \arctan (k_\\perp/k_\\parallel)`
        for the sample points generated for the interpolation.

    Returns
    -------
    r_n : array-like
        1D array of radii.
    phi_n : array-like
        2D array of azimuthal angles with shape (ndim-1, N), where N is the number of points.
        phi_n[0,:] :math:`\\in [0,2*\\pi]`, and phi_n[1:,:] :math:`\\in [0,\\pi]`.
    """

    def generator():
        r_n, phi_n = regular_angular_generator(
            bins, dims2avg, angular_resolution=angular_resolution
        )
        # sine because the phi_n are wrt x-axis and we need them wrt z-axis.
        if len(phi_n) == 1:
            mask = np.sin(phi_n[0, :]) >= mu
        else:
            mask = np.all(np.sin(phi_n[1:, :]) >= mu, axis=0)
        return r_n[mask], phi_n[:, mask]

    return generator()


def regular_angular_generator(bins, dims2avg, angular_resolution=0.1):
    r"""
    Returns a set of spherical coordinates regularly sampled at a given angular resolution.

    Parameters
    ----------
    bins : array-like
        1D array of radii at which we want to spherically average the field.
    dims2avg : int
        The number of dimensions to average over.
    angular_resolution : float, optional
        The angular resolution in radians for the sample points for the interpolation.
        Defaults to 0.1 rad.

    Returns
    -------
    r_n : array-like
        1D array of radii.
    phi_n : array-like
        2D array of azimuthal angles with shape (ndim-1, N), where N is the number of points.
        phi_n[0,:] :math:`\in [0,2*\pi]`, and phi_n[1:,:] :math:`\in [0,\pi]`.
    """

    def generator():
        num_angular_bins = np.array(
            np.max(
                [
                    np.round(2 * np.pi * bins / angular_resolution),
                    np.ones_like(bins) * 100,
                ],
                axis=0,
            ),
            dtype=int,
        )
        phi_i = [np.linspace(0.0, np.pi, n) for n in num_angular_bins]
        phi_N = [np.linspace(0.0, 2 * np.pi, n) for n in num_angular_bins]

        # Angular resolution is same for all dims
        phi_n = np.concatenate(
            [
                np.array(
                    np.meshgrid(
                        *([phi_N[i]] + [phi_i[i]] * (dims2avg - 1)), sparse=False
                    )
                ).reshape((dims2avg, num_angular_bins[i] ** dims2avg))
                for i in range(len(bins))
            ],
            axis=-1,
        )
        r_n = np.concatenate(
            [[r] * (num_angular_bins[i] ** dims2avg) for i, r in enumerate(bins)]
        )
        return r_n, phi_n

    return generator()


def _sample_coords_interpolate(coords, bins, weights, interp_points_generator):
    # Grid is regular + can be ordered only in Cartesian coords.
    field_shape = [len(c) for c in coords]
    if isinstance(weights, np.ndarray):
        weights = weights.reshape(field_shape)
    else:
        weights = np.ones(field_shape) * weights
    # To extrapolate at the edges if needed.
    # Evaluate it on points in angular coords that we then convert to Cartesian.
    # Number of angular bins for each radius absk on which to calculate the interpolated power when doing the averaging
    # Larger wavemodes / radii will have more samples in theta
    # "bins" is always 1D
    # Max is to set a minimum number of bins for the smaller wavemode bins
    if interp_points_generator is None:
        interp_points_generator = regular_angular_generator
    if len(coords) > 1:
        r_n, phi_n = interp_points_generator(bins, len(coords) - 1)
        sample_coords = _spherical2cartesian(r_n, phi_n)
    else:
        sample_coords = bins.reshape(1, -1)
        r_n = bins

    # Remove sample coords that are not even on the coords grid (e.g. due to phi)
    mask1 = np.all(
        sample_coords >= np.array([c.min() for c in coords])[..., np.newaxis], axis=0
    )
    mask2 = np.all(
        sample_coords <= np.array([c.max() for c in coords])[..., np.newaxis], axis=0
    )

    mask = mask1 & mask2
    sample_coords = sample_coords[:, mask]
    r_n = r_n[mask]
    if len(r_n) == 0:
        raise ValueError(
            "Generated sample points are outside of the coordinate box provided for the field! Try changing your points generator or field coordinates."
        )
    return sample_coords, r_n


def _field_average_interpolate(coords, field, bins, weights, sample_coords, r_n):
    # Grid is regular + can be ordered only in Cartesian coords.
    if isinstance(weights, np.ndarray):
        weights = weights.reshape(field.shape)
    else:
        weights = np.ones_like(field) * weights
    # if field.dtype.kind == "c":
    #    field = np.array(field, dtype=np.complex64)
    # else:
    #    field = np.array(field, dtype=np.float32)
    # Set 0 weights to NaNs
    field = field * weights
    field[weights == 0] = np.nan
    # Rescale the field (see scipy documentation for RegularGridInterpolator)
    mean, std = np.nanmean(field), np.max(
        [np.nanstd(field), 1.0]
    )  # Avoid division by 0
    rescaled_field = (field - mean) / std
    fnc = RegularGridInterpolator(
        coords,
        rescaled_field,  # Complex data is accepted.
        bounds_error=False,
        fill_value=np.nan,
    )  # To extrapolate at the edges if needed.
    # Evaluate it on points in angular coords that we then convert to Cartesian.

    interped_field = fnc(sample_coords.T) * std + mean
    if np.all(np.isnan(interped_field)):
        warnings.warn("Interpolator returned all NaNs.", stacklevel=2)
    # Average over the spherical shells for each radius / bin value
    avged_field = np.array([np.nanmean(interped_field[r_n == b]) for b in bins])
    sumweights = np.unique(r_n[~np.isnan(interped_field)], return_counts=True)[1]
    return avged_field, sumweights


def _field_average(indx, field, weights, sumweights):
    if not np.isscalar(weights) and field.shape != weights.shape:
        raise ValueError(
            "the field and weights must have the same shape!",
            field.shape,
            weights.shape,
        )

    field = field * weights  # Leave like this because field is mutable

    rl = (
        np.bincount(
            indx, weights=np.real(field.flatten()), minlength=len(sumweights) + 2
        )[1:-1]
        / sumweights
    )
    if field.dtype.kind == "c":
        im = (
            1j
            * np.bincount(
                indx, weights=np.imag(field.flatten()), minlength=len(sumweights) + 2
            )[1:-1]
            / sumweights
        )
    else:
        im = 0

    return rl + im


def _field_variance(indx, field, average, weights, V1):
    if field.dtype.kind == "c":
        raise NotImplementedError(
            "Cannot use a complex field when computing variance, yet."
        )

    # Create a full flattened array of the same shape as field, with the average in that bin.
    # We have to pad the average vector with 0s on either side to account for cells outside the bin range.
    average_field = np.concatenate(([0], average, [0]))[indx]

    # Create the V2 array
    if not np.isscalar(weights):
        weights = weights.flatten()
        V2 = np.bincount(indx, weights=weights**2, minlength=len(V1) + 2)[1:-1]
    else:
        V2 = V1

    field = (field.flatten() - average_field) ** 2 * weights

    # This res is the estimated variance of each cell in the bin
    res = np.bincount(indx, weights=field, minlength=len(V1) + 2)[1:-1] / (V1 - V2 / V1)

    # Modify to the estimated variance of the sum of the cells in the bin.
    res *= V2 / V1**2

    return res


def angular_average_nd(  # noqa: C901
    field,
    coords,
    bins,
    n=None,
    weights=1,
    average=True,
    bin_ave=True,
    get_variance=False,
    log_bins=False,
    interpolation_method=None,
    interp_points_generator=None,
    return_sumweights=False,
):
    """
    Average the first n dimensions of a given field within radial bins.

    This function be used to take "hyper-cylindrical" averages of fields. For a 3D field, with `n=2`, this is exactly
    a cylindrical average. This function can be used in fields of arbitrary dimension (memory permitting), and the field
    need not be centred at the origin. The averaging assumes that the grid cells fall completely into the bin which
    encompasses the co-ordinate point for the cell (i.e. there is no weighted splitting of cells if they intersect a bin
    edge).

    It is optimized for applying a set of weights, and obtaining the variance of the mean, at the same time as
    averaging.

    Parameters
    ----------
    field : md-array
        An array of arbitrary dimension specifying the field to be angularly averaged.

    coords : list of n arrays
        A list of 1D arrays specifying the co-ordinates in each dimension *to be averaged*.

    bins : int or array.
        Specifies the radial bins for the averaged dimensions. Can be an int or array specifying radial bin edges.

    n : int, optional
        The number of dimensions to be averaged. By default, all dimensions are averaged. Always uses
        the first `n` dimensions.

    weights : array, optional
        An array of the same shape as the first `n` dimensions of `field`, giving a weight for each entry.

    average : bool, optional
        Whether to take the (weighted) average. If False, returns the (unweighted) sum.

    bin_ave : bool, optional
        Whether to return the bin co-ordinates as the (weighted) average of cells within the bin (if True), or
        the linearly spaced edges of the bins

    get_variance : bool, optional
        Whether to also return an estimate of the variance of the power in each bin.

    log_bins : bool, optional
        Whether to create bins in log-space.

    interpolation_method : str, optional
        If None, does not interpolate. Currently only 'linear' is supported.

    interp_points_generator : callable, optional
        A function that generates the sample points for the interpolation.
        If None, defaults regular_angular_generator(resolution = 0.1).
        If callable, a function that takes as input an angular resolution for the sampling
        and returns a 1D array of radii and 2D array of angles
        (see documentation on the inputs of _sphere2cartesian for more details on the outputs).
        This function can be used to obtain an angular average over a certain region of the field by
        limiting where the samples are taken for the interpolation. See function above_mu_min_generator
        for an example of such a function.

    return_sumweights : bool, optional
        Whether to return the number of modes in each bin.
        Note that for the linear interpolation case,
        this corresponds to the number of samples averaged over
        (which can be adjusted by supplying a different interp_points_generator
        function with a different angular resolution).

    Returns
    -------
    field : (m-n+1)-array
        The angularly-averaged field. The first dimension corresponds to `bins`, while the rest correspond to the
        unaveraged dimensions.

    bins : 1D-array
        The radial co-ordinates of the bins. Either the mean co-ordinate from the input data, or the regularly spaced
        bins, dependent on `bin_ave`.

    var : (m-n+1)-array, optional
        The variance of the averaged field (same shape as `field`), estimated from the mean standard error.
        Only returned if `get_variance` is True.

    Examples
    --------
    Create a 3D radial function, and average over radial bins. Equivalent to calling :func:`angular_average`:

    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> x = np.linspace(-5,5,128)   # Setup a grid
    >>> X,Y,Z = np.meshgrid(x,x,x)  # ""
    >>> r = np.sqrt(X**2+Y**2+Z**2) # Get the radial co-ordinate of grid
    >>> field = np.exp(-r**2)       # Generate a radial field
    >>> avgfunc, bins, _ = angular_average_nd(field,[x,x,x],bins=100)   # Call angular_average
    >>> plt.plot(bins, np.exp(-bins**2), label="Input Function")   # Plot input function versus ang. avg.
    >>> plt.plot(bins, avgfunc, label="Averaged Function")

    Create a 2D radial function, extended to 3D, and average over first 2 dimensions (cylindrical average):

    >>> r = np.sqrt(X**2+Y**2)
    >>> field = np.exp(-r**2)    # 2D field
    >>> field = np.repeat(field,len(x)).reshape((len(x),)*3)   # Extended to 3D
    >>> avgfunc, avbins, coords = angular_average_nd(field, [x,x,x], bins=50, n=2)
    >>> plt.plot(avbins, np.exp(-avbins**2), label="Input Function")
    >>> plt.plot(avbins, avgfunc[:,0], label="Averaged Function")
    """
    if n is None:
        n = len(coords)

    if len(coords) != len(field.shape):
        raise ValueError("coords should be a list of arrays, one for each dimension.")

    if interpolation_method is not None and interp_points_generator is None:
        interp_points_generator = regular_angular_generator

    if interpolation_method is not None and interpolation_method != "linear":
        raise ValueError("Only linear interpolation is supported.")

    if n == len(coords):
        return angular_average(
            field,
            coords,
            bins,
            weights,
            average,
            bin_ave,
            get_variance,
            log_bins=log_bins,
            interpolation_method=interpolation_method,
            interp_points_generator=interp_points_generator,
            return_sumweights=return_sumweights,
        )

    if len(coords) == len(field.shape):
        # coords are a segmented list of dimensional co-ordinates
        coords_grid = _magnitude_grid([c for i, c in enumerate(coords) if i < n])
    elif interpolation_method is not None:
        raise ValueError(
            "Must supply a list of len(field.shape) of 1D coordinate arrays for coords when interpolating!"
        )
    else:
        # coords are the magnitude of the co-ordinates
        # since we are not interpolating, then we can just use the magnitude of the co-ordinates
        coords_grid = coords

    coords_grid = _magnitude_grid([c for i, c in enumerate(coords) if i < n])
    n1 = np.prod(field.shape[:n])
    n2 = np.prod(field.shape[n:])
    if interpolation_method is None:
        indx, bins, sumweights = _get_binweights(
            coords_grid, weights, bins, average, bin_ave=bin_ave, log_bins=log_bins
        )
        res = np.zeros((len(sumweights), n2), dtype=field.dtype)
    if interpolation_method is not None:
        bins = _getbins(bins, coords_grid, log_bins)
        if log_bins:
            bins = np.exp((np.log(bins[1:]) + np.log(bins[:-1])) / 2)
        else:
            bins = (bins[1:] + bins[:-1]) / 2
        res = np.zeros((len(bins), n2), dtype=field.dtype)

    if get_variance:
        var = np.zeros_like(res)

    for i, fld in enumerate(field.reshape((n1, n2)).T):
        try:
            w = weights.flatten()
        except AttributeError:
            w = weights
        if interpolation_method is None:
            res[:, i] = _field_average(indx, fld, w, sumweights)
            if get_variance:
                var[:, i] = _field_variance(indx, fld, res[:, i], w, sumweights)
        elif interpolation_method == "linear":
            sample_coords, r_n = _sample_coords_interpolate(
                coords[:n], bins, weights, interp_points_generator
            )
            res[:, i], sumweights = _field_average_interpolate(
                coords[:n], fld.reshape(field.shape[:n]), bins, w, sample_coords, r_n
            )
            if get_variance:
                # TODO: Implement variance calculation for interpolation
                raise NotImplementedError(
                    "Variance calculation not implemented for interpolation"
                )

    if not get_variance:
        if return_sumweights:
            return res.reshape((len(sumweights),) + field.shape[n:]), bins, sumweights
        else:
            return res.reshape((len(sumweights),) + field.shape[n:]), bins
    else:
        if return_sumweights:
            return (
                res.reshape((len(sumweights),) + field.shape[n:]),
                bins,
                var,
                sumweights,
            )
        else:
            return res.reshape((len(sumweights),) + field.shape[n:]), bins, var


def power2delta(freq: list):
    r"""
    Convert power P(k) to dimensionless power.

    Calculate the multiplicative factor :math:`\Omega_d |k|^d / (2 \pi)^d`,
    where :math:`\Omega_d = \frac{2 \pi^{d/2}}{\Gamma(d/2)}` needed to convert
    the power P(k) (in 3D :math:`\rm{[mK}^2 \rm{k}^{-3}]`) into the "dimensionless" power spectrum
    :math:`\Delta^2_{21}` (in 3D :math:`\rm{[mK}^2]`).

    Parameters
    ----------
    freq : list
        A list containing 1D arrays of wavemodes k1, k2, k3, ...

    Returns
    -------
    prefactor : np.ndarray
        An array of shape (len(k1), len(k2), len(k3), ...) containing the values of the prefactor
        :math:`\Omega_d |k|^d / (2 \pi)^d`, where :math:`\Omega_d = \frac{2 \pi^{d/2}}{\Gamma(d/2)}`
        is the solid angle and :math:`\Gamma` is the gamma function. For a 3-D sphere, the prefactor
        is :math:`|k|^3 / (2\pi^2)`.

    """
    shape = [len(f) for f in freq]
    dim = len(shape)
    coords = np.meshgrid(*freq, sparse=True)
    squares = [c**2 for c in coords]
    absk = np.sqrt(sum(squares))
    solid_angle = 2 * np.pi ** (dim / 2) / gamma(dim / 2)
    prefactor = solid_angle * (absk / (2 * np.pi)) ** dim
    return prefactor


def ignore_zero_absk(freq: list, kmag: np.ndarray | None):
    r"""
    Returns a mask with zero weights where :math:`|k| = 0`.

    Parameters
    ----------
    freq : list
        A list containing three arrays of wavemodes k1, k2, k3, ...
    res_ndim : int, optional
        Only perform angular averaging over first `res_ndim` dimensions. By default,
        uses all dimensions.

    Returns
    -------
    k_weights : np.ndarray
        An array of same shape as the averaged field containing the weights of the k-modes.
        For example, if the field is not averaged (e.g. 3D power), then the shape is
        (len(k1), len(k2), len(k3)).

    """
    k_weights = ~np.isclose(kmag, 0)
    return k_weights


def ignore_zero_ki(freq: list, kmag: np.ndarray = None):
    r"""
    Returns a mask with zero weights where k_i == 0, where i = x, y, z for a 3D field.

    Parameters
    ----------
    freq : list
        A list containing 1D arrays of wavemodes k1, k2, k3, ...
    res_ndim : int, optional
        Only perform angular averaging over first `res_ndim` dimensions. By default,
        uses all dimensions.

    Returns
    -------
    k_weights : np.ndarray
        An array of same shape as the averaged field containing the weights of the k-modes.
        For example, if the field is not averaged (e.g. 3D power), then the shape is
        (len(k1), len(k2), len(k3)).
    """
    res_ndim = len(kmag.shape)

    coords = np.array(np.meshgrid(*freq[:res_ndim], sparse=False))
    k_weights = np.all(coords != 0, axis=0)

    return k_weights


def discretize_N(
    deltax,
    boxlength,
    deltax2=None,
    N=None,
    weights=None,
    weights2=None,
    dimensionless=True,
):
    r"""
    Perform binning of a field to obtain a discrete sampling of deltax.

    Parameters
    ----------
    deltax : array-like
        The field on which to calculate the power spectrum . Can either be arbitrarily
        n-dimensional, or 2-dimensional with the first being the number of spatial
        dimensions, and the second the positions of discrete particles in the field. The
        former should represent a density field, while the latter is a discrete sampling
        of a field. This function chooses which to use by checking the value of ``N``
        (see below). Note that if a discrete sampling is used, the power spectrum
        calculated is the "overdensity" power spectrum, i.e. the field re-centered about
        zero and rescaled by the mean.
    boxlength : float or list of floats
        The length of the box side(s) in real-space.
    deltax2 : array-like
        If given, a box of the same shape as deltax, against which deltax will be cross
        correlated.
    N : int, optional
        The number of grid cells per side in the box. Only required if deltax is a
        discrete sample. If given, the function will assume a discrete sample.
    res_ndim : int, optional
        Only perform angular averaging over first `res_ndim` dimensions. By default,
        uses all dimensions.
    weights, weights2 : array-like, optional
        If deltax is a discrete sample, these are weights for each point.
    dimensionless: bool, optional
        Whether to normalise the cube by its mean prior to taking the power.

    Returns
    -------
    deltax : array-like
        The field on which to calculate the power spectrum . Can either be arbitrarily
        n-dimensional, or 2-dimensional with the first being the number of spatial
        dimensions, and the second the positions of discrete particles in the field. The
        former should represent a density field, while the latter is a discrete sampling
        of a field. This function chooses which to use by checking the value of ``N``
        (see below). Note that if a discrete sampling is used, the power spectrum
        calculated is the "overdensity" power spectrum, i.e. the field re-centered about
        zero and rescaled by the mean.
    deltax2 : array-like
        If given, a box of the same shape as deltax, against which deltax will be cross
        correlated.
    Npart1, Npart2 : array-like
        Length of first dimension of deltax and deltax2, respectively.
    dim : int
        Length of second dimension of deltax.
    N : array-like
        The number of grid cells per side in the box. Only required if deltax is a
        discrete sample. If given, the function will assume a discrete sample.
    boxlength : float or list of floats
        The length of the box side(s) in real-space.

    """
    if deltax.shape[1] > deltax.shape[0]:
        raise ValueError(
            "It seems that there are more dimensions than particles! "
            "Try transposing deltax."
        )

    if deltax2 is not None and deltax2.shape[1] > deltax2.shape[0]:
        raise ValueError(
            "It seems that there are more dimensions than particles! "
            "Try transposing deltax2."
        )

    dim = deltax.shape[1]
    if deltax2 is not None and dim != deltax2.shape[1]:
        raise ValueError("deltax and deltax2 must have the same number of dimensions!")

    if not np.iterable(N):
        N = [N] * dim

    if not np.iterable(boxlength):
        boxlength = [boxlength] * dim

    Npart1 = deltax.shape[0]

    Npart2 = deltax2.shape[0] if deltax2 is not None else Npart1

    # Generate a histogram of the data, with appropriate number of bins.
    edges = [np.linspace(0, L, n + 1) for L, n in zip(boxlength, N)]

    deltax = np.histogramdd(deltax % boxlength, bins=edges, weights=weights)[0].astype(
        "float"
    )

    if deltax2 is not None:
        deltax2 = np.histogramdd(deltax2 % boxlength, bins=edges, weights=weights2)[
            0
        ].astype("float")

    # Convert sampled data to mean-zero data
    if dimensionless:
        deltax = deltax / np.mean(deltax) - 1
        if deltax2 is not None:
            deltax2 = deltax2 / np.mean(deltax2) - 1
    else:
        deltax -= np.mean(deltax)
        if deltax2 is not None:
            deltax2 -= np.mean(deltax2)
    return deltax, deltax2, Npart1, Npart2, dim, N, boxlength


def get_power(
    deltax,
    boxlength,
    deltax2=None,
    N=None,
    a=1.0,
    b=1.0,
    remove_shotnoise=True,
    vol_normalised_power=True,
    bins=None,
    res_ndim=None,
    weights=None,
    weights2=None,
    dimensionless=True,
    bin_ave=True,
    get_variance=False,
    log_bins=False,
    ignore_zero_mode=False,
    k_weights=1,
    nthreads=None,
    prefactor_fnc=None,
    interpolation_method=None,
    interp_points_generator=None,
    return_sumweights=False,
):
    r"""
    Calculate isotropic power spectrum of a field, or cross-power of two similar fields.

    This function, by default, conforms to typical cosmological power spectrum
    conventions -- normalising by the volume of the box and removing shot noise if
    applicable. These options are configurable.

    Parameters
    ----------
    deltax : array-like
        The field on which to calculate the power spectrum . Can either be arbitrarily
        n-dimensional, or 2-dimensional with the first being the number of spatial
        dimensions, and the second the positions of discrete particles in the field. The
        former should represent a density field, while the latter is a discrete sampling
        of a field. This function chooses which to use by checking the value of ``N``
        (see below). Note that if a discrete sampling is used, the power spectrum
        calculated is the "overdensity" power spectrum, i.e. the field re-centered about
        zero and rescaled by the mean.
    boxlength : float or list of floats
        The length of the box side(s) in real-space.
    deltax2 : array-like
        If given, a box of the same shape as deltax, against which deltax will be cross
        correlated.
    N : int, optional
        The number of grid cells per side in the box. Only required if deltax is a
        discrete sample. If given, the function will assume a discrete sample.
    a,b : float, optional
        These define the Fourier convention used. See :mod:`powerbox.dft` for details.
        The defaults define the standard usage in *cosmology* (for example, as defined
        in Cosmological Physics, Peacock, 1999, pg. 496.). Standard numerical usage
        (eg. numpy) is ``(a,b) = (0,2pi)``.
    remove_shotnoise : bool, optional
        Whether to subtract a shot-noise term after determining the isotropic power.
        This only affects discrete samples.
    vol_weighted_power : bool, optional
        Whether the input power spectrum, ``pk``, is volume-weighted. Default True
        because of standard cosmological usage.
    bins : int or array, optional
        Defines the final k-bins output. If None, chooses a number based on the input
        resolution of the box. Otherwise, if int, this defines the number of kbins, or
        if an array, it defines the exact bin edges.
    res_ndim : int, optional
        Only perform angular averaging over first `res_ndim` dimensions. By default,
        uses all dimensions.
    weights, weights2 : array-like, optional
        If deltax is a discrete sample, these are weights for each point.
    dimensionless: bool, optional
        Whether to normalise the cube by its mean prior to taking the power.
    bin_ave : bool, optional
        Whether to return the bin co-ordinates as the (weighted) average of cells within
        the bin (if True), or the linearly spaced edges of the bins
    get_variance : bool, optional
        Whether to also return an estimate of the variance of the power in each bin.
    log_bins : bool, optional
        Whether to create bins in log-space.
    ignore_zero_mode : bool, optional
        Whether to ignore the k=0 mode (or DC term).
    k_weights : nd-array or callable optional
        The weights of the n-dimensional k modes. This can be used to filter out some
        modes completely. If callable, a function that takes in a a list containing
        arrays of wavemodes [k1, k2, k3, ...] as well as kmag (optional), and returns an array
        of weights of shape (len(k1), len(k2), len(k3), ... ) for a res_ndim = 1.
    nthreads : bool or int, optional
        If set to False, uses numpy's FFT routine. If set to None, uses pyFFTW with
        number of threads equal to the number of available CPUs. If int, uses pyFFTW
        with number of threads equal to the input value.
    prefactor_fnc : callable, optional
        A function that takes in a list containing arrays of wavemodes [k1, k2, k3, ...]
        and returns an array of the same size. This function is applied on the FT before
        the angular averaging. It can be used, for example, to convert linearly-binned
        power into power-per-logarithmic k ($\Delta^2$).
    interpolation_method : str, optional
        If None, does not interpolate. Currently only 'linear' is supported.
    interp_points_generator : callable, optional
        A function that generates the sample points for the interpolation.
        If None, defaults regular_angular_generator(resolution = 0.1).
        If callable, a function that takes as input an angular resolution for the sampling
        and returns a 1D array of radii and 2D array of angles
        (see documentation on the inputs of _sphere2cartesian for more details on the outputs).
        This function can be used to obtain an angular average over a certain region of the field by
        limiting where the samples are taken for the interpolation. See function above_mu_min_generator
        for an example of such a function.
    return_sumweights : bool, optional
        Whether to return the number of modes in each bin.
        Note that for the linear interpolation case,
        this corresponds to the number of samples averaged over
        (which can be adjusted with the angular_resolution parameter).

    Returns
    -------
    p_k : array
        The power spectrum averaged over bins of equal :math:`|k|`.
    meank : array
        The bin-centres for the p_k array (in k). This is the mean k-value for cells in
        that bin.
    var : array
        The variance of the power spectrum, estimated from the mean standard error. Only
        returned if `get_variance` is True.

    Examples
    --------
    One can use this function to check whether a box created with :class:`PowerBox`
    has the correct power spectrum:

    >>> from powerbox import PowerBox
    >>> import matplotlib.pyplot as plt
    >>> pb = PowerBox(250,lambda k : k**-2.)
    >>> p,k = get_power(pb.delta_x,pb.boxlength)
    >>> plt.plot(k,p)
    >>> plt.plot(k,k**-2.)
    >>> plt.xscale('log')
    >>> plt.yscale('log')

    An example of a prefactor_fnc applied to the box in the above example:

    >>> from powerbox import get_power
    >>> import numpy as np
    >>> def power2delta(freq):
    >>>     kx = freq[0]
    >>>     ky = freq[1]
    >>>     kz = freq[2]
    >>>     absk = np.sqrt(np.add.outer(np.add.outer(kx**2,ky**2), kz**2))
    >>>     return absk ** 3 / (2 * np.pi ** 2)
    >>> p, k = get_power(pb.delta_x, pb.boxlength, prefactor_fnc=power2delta)
    """
    # Check if the input data is in sampled particle format
    if N is not None:
        deltax, deltax2, Npart1, Npart2, dim, N, boxlength = discretize_N(
            deltax,
            boxlength,
            deltax2=deltax2,
            N=N,
            weights=weights,
            weights2=weights2,
            dimensionless=dimensionless,
        )

    else:
        # If input data is already a density field, just get the dimensions.
        dim = len(deltax.shape)

        if not np.iterable(boxlength):
            boxlength = [boxlength] * dim

        if deltax2 is not None and deltax.shape != deltax2.shape:
            raise ValueError("deltax and deltax2 must have the same shape!")

        N = deltax.shape
        Npart1 = None

    V = np.prod(boxlength)

    # Calculate the n-D power spectrum and align it with the k from powerbox.
    FT, freq, k = dft.fft(
        deltax, L=boxlength, a=a, b=b, ret_cubegrid=True, nthreads=nthreads
    )

    FT2 = (
        dft.fft(deltax2, L=boxlength, a=a, b=b, nthreads=nthreads)[0]
        if deltax2 is not None
        else FT
    )

    P = np.real(FT * np.conj(FT2) / V**2)

    if vol_normalised_power:
        P *= V

    if prefactor_fnc is not None:
        P *= prefactor_fnc(freq)

    if res_ndim is None:
        res_ndim = dim

    # Determine a nice number of bins.
    if bins is None:
        bins = int(np.prod(N[:res_ndim]) ** (1.0 / res_ndim) / 2.2)

    kmag = _magnitude_grid([c for i, c in enumerate(freq) if i < res_ndim])

    if np.isscalar(k_weights):
        k_weights = np.ones_like(kmag)

    if callable(k_weights):
        k_weights = k_weights(freq, kmag)

    # Set k_weights so that k=0 mode is ignore if desired.
    if ignore_zero_mode:
        k_weights = np.logical_and(k_weights, ignore_zero_absk(freq, kmag))

    # res is (P, k, <var>, <sumweights>)
    res = angular_average_nd(
        P,
        freq,
        bins,
        n=res_ndim,
        bin_ave=bin_ave,
        get_variance=get_variance,
        log_bins=log_bins,
        weights=k_weights,
        interpolation_method=interpolation_method,
        interp_points_generator=interp_points_generator,
        return_sumweights=return_sumweights,
    )
    res = list(res)
    # Remove shot-noise
    if remove_shotnoise and Npart1:
        res[0] -= np.sqrt(V**2 / Npart1 / Npart2)

    return res + [freq[res_ndim:]] if res_ndim < dim else res
