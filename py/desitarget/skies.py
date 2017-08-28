# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
"""
==================
desitarget.skies
==================

Module dealing with the assignation of sky fibers for target selection
"""
from __future__ import (absolute_import, division)
#
import numpy as np
import fitsio
from time import time

from astropy.coordinates import SkyCoord
from astropy import units as u

from desiutil import brick
from desitarget import io


def density_of_sky_fibers(margin=2.):
    """Use desihub products to find required density of sky fibers for DESI

    Parameters
    ----------
    margin : :class:`float`, optional, defaults to 2.
        Factor of extra sky positions to generate. So, for margin=2, twice as
        many sky positions as the default requirements will be generated

    Returns
    -------
    :class:`float`
        The density of sky fibers to generate
    """

    from desimodel.io import load_fiberpos, load_target_info
    fracsky = load_target_info()["frac_sky"]
    nfibers = len(load_fiberpos())
    nskymin = margin*fracsky*nfibers

    return nskymin


def calculate_separations(objs,navoid=2):
    """Generate an array of separations (in arcseconds) for a set of objects

    Parameters
    ----------
    objs : :class:`~numpy.ndarray`
        numpy structured array with UPPERCASE columns, OR a string 
        tractor/sweep filename. Must contain at least the columns
        "RA", "DEC", "SHAPEDEV_R", "SHAPEEXP_R"
    navoid : :class:`float`, optional, defaults to 2.
        the number of times the galaxy half-light radius (or seeing) to avoid
        objects out to when placing sky fibers

    Returns
    -------
    :class:`float`
        an array of maximum separations (in arcseconds) based on 
        de Vaucouleurs, Exponential or point-source half-light radii
    """

    nobjs = len(objs)

    #ADM possible choices for separation based on de Vaucouleurs and Exponential profiles
    #ADM or a minimum of 2 arcseconds for point sources ("the seeing")
    sepchoices = np.vstack([objs["SHAPEDEV_R"], objs["SHAPEEXP_R"], np.ones(nobjs)*2]).T

    #ADM the maximum separation from de Vaucoulers/exponential/PSF choices
    sep = navoid*np.max(sepchoices,axis=1)

    return sep


def generate_sky_positions(objs,navoid=2.,nskymin=None):
    """Use a basic avoidance-of-other-objects approach to generate sky positions

    Parameters
    ----------
    objs : :class:`~numpy.ndarray` 
        numpy structured array with UPPERCASE columns, OR a string 
        tractor/sweep filename. Must contain at least the columns
        "RA", "DEC", "SHAPEDEV_R", "SHAPEEXP_R"
    navoid : :class:`float`, optional, defaults to 2.
        the number of times the galaxy half-light radius (or seeing) to avoid
        objects out to when placing sky fibers
    nskymin : :class:`float`, optional, defaults to reading from desimodel.io
        the minimum DENSITY of sky fibers to generate

    Returns
    -------
    ragood : :class:`~numpy.array`
        array of RA coordinates for good sky positions
    decgood : :class:`~numpy.array`
        array of Dec coordinates for good sky positions
    rabad : :class:`~numpy.array`
        array of RA coordinates for bad sky positions, i.e. positions that
        ARE within navoid half-light radii of a galaxy (or navoid*2 arcseconds
        for a PSF object)
    decbad : :class:`~numpy.array`
        array of Dec coordinates for bad sky positions, i.e. positions that
        ARE within navoid half-light radii of a galaxy (or navoid*2 arcseconds
        for a PSF object)
    """
    #ADM set up the default log
    from desiutil.log import get_logger, DEBUG
    log = get_logger(DEBUG)

    start = time()

    log.info('Generating sky positions...t = {:.1f}s'.format(time()-start))

    #ADM check if input objs is a filename or the actual data
    if isinstance(objs, str):
        objs = io.read_tractor(objs)
    nobjs = len(objs)

    #ADM an avoidance separation (in arcseconds) for each
    #ADM object based on its half-light radius/profile
    log.info('Calculating avoidance zones...t = {:.1f}s'.format(time()-start))
    sep = calculate_separations(objs,navoid)
    #ADM the maximum such separation for any object in the passed set in arcsec
    maxrad = max(sep)

    #ADM if needed, determine the minimum density of sky fibers to generate
    if nskymin is None:
        nskymin = density_of_sky_fibers()

    #ADM the coordinate limits and corresponding area of the passed objs
    ramin, ramax = np.min(objs["RA"]), np.max(objs["RA"])
    #ADM guard against the wraparound bug (should never be an issue for the sweeps, anyway)
    if ramax - ramin > 180.:
        ramax -= 360.
    decmin, decmax = np.min(objs["DEC"]),np.max(objs["DEC"])
    sindecmin, sindecmax = np.sin(np.radians(decmin)), np.sin(np.radians(decmax))
    spharea = (ramax-ramin)*np.degrees(sindecmax-sindecmin)

    #ADM how many sky positions we need to generate to meet the minimum density requirements
    nskies = int(spharea*nskymin)
    #ADM how many sky positions to generate, given that we'll reject objects close to bad
    #ADM sources. The factor of 10 was derived by trial-and-error...but this doesn't need
    #ADM to be optimal as this algorithm should become more sophisticated
    nchunk = nskies*10

    #ADM arrays of GOOD sky positions to populate with coordinates
    ragood, decgood = np.empty(nskies), np.empty(nskies)
    #ADM lists of BAD sky positions to populate with coordinates
    rabad, decbad = [], []

    #ADM ngenerate will become zero when we generate enough GOOD sky positions
    ngenerate = nskies

    while ngenerate:

        #ADM generate random points in RA and Dec (correctly distributed on the sphere)
        log.info('Generated {} test positions...t = {:.1f}s'.format(nchunk,time()-start))
        ra = np.random.uniform(ramin,ramax,nchunk)
        dec = np.degrees(np.arcsin(1.-np.random.uniform(1-sindecmax,1-sindecmin,nchunk)))

        #ADM set up the coordinate objects
        cskies = SkyCoord(ra*u.degree, dec*u.degree)
        cobjs = SkyCoord(objs["RA"]*u.degree, objs["DEC"]*u.degree)

        #ADM split the objects up using a separation of 4*navoid arcseconds in order to
        #ADM speed up the coordinate matching when we have some objects with large radii
        sepsplit = 4*navoid
        bigsepw = np.where(sep > sepsplit)[0]
        smallsepw = np.where(sep <= sepsplit)[0]

        #ADM set up a list of skies that don't match an object
        goodskies = np.ones(len(cskies),dtype=bool)

        #ADM match the small-separation objects and flag any skies that match such an object
        log.info('Matching positions out to {:.1f} arcsec...t = {:.1f}s'
                 .format(sepsplit,time()-start))
        idskies, idobjs, d2d, _ = cobjs[smallsepw].search_around_sky(cskies,sepsplit*u.arcsec)
        w = np.where(d2d.arcsec < sep[smallsepw[idobjs]])
        goodskies[idskies[w]] = False

        #ADM match the large-separation objects and flag any skies that match such an object
        log.info('Matching additional positions out to {:.1f} arcsec...t = {:.1f}s'
                 .format(maxrad,time()-start))
        idskies, idobjs, d2d, _ = cobjs[bigsepw].search_around_sky(cskies,maxrad*u.arcsec)
        w = np.where(d2d.arcsec < sep[bigsepw[idobjs]])
        goodskies[idskies[w]] = False

        #ADM good sky positions we found
        wgood = np.where(goodskies)[0]
        n1 = nskies - ngenerate
        ngenerate = max(0, ngenerate - len(wgood))
        n2 = nskies - ngenerate
        ragood[n1:n2] = ra[wgood[:n2 - n1]]
        decgood[n1:n2] = dec[wgood[:n2 - n1]]
        log.info('Need to generate a further {} positions...t = {:.1f}s'.format(ngenerate,time()-start))

        #ADM bad sky positions we found
        wbad = np.where(~goodskies)[0]
        rabad.append(list(ra[wbad]))
        decbad.append(list(dec[wbad]))

    #ADM we potentially created nested lists for the bad skies, so need to flatten
    #ADM also scale the bad skies by area (based on the number of good skies)
    nbad = int(1.*nskies*len(wbad)/len(wgood))
    rabad = np.array([item for sublist in rabad for item in sublist])[:nbad]
    decbad = np.array([item for sublist in decbad for item in sublist])[:nbad]

    log.info('Done...t = {:.1f}s'.format(time()-start))

    return ragood, decgood, rabad, decbad


def plot_sky_positions(ragood,decgood,rabad,decbad,objs,navoid=2.,plotname=None):
    """plot an example set of sky positions to check if they avoid real objects

    Parameters
    ----------
    ragood : :class:`~numpy.array`
        array of RA coordinates for good sky positions
    decgood : :class:`~numpy.array`
        array of Dec coordinates for good sky positions
    rabad : :class:`~numpy.array`
        array of RA coordinates for bad sky positions, i.e. positions that
        ARE within the avoidance zones of the "objs"
    decbad : :class:`~numpy.array`
        array of Dec coordinates for bad sky positions, i.e. positions that
        ARE within the avoidance zones of the "objs"
    objs : :class:`~numpy.ndarray` 
        numpy structured array with UPPERCASE columns, OR a string 
        tractor/sweep filename. Must contain at least the columns
        "RA", "DEC", "SHAPEDEV_R", "SHAPEEXP_R"
    navoid : :class:`float`, optional, defaults to 2.
        the number of times the galaxy half-light radius (or seeing) that
        objects (objs) were avoided out to when generating sky positions
    plotname : :class:`str`, defaults to None    
        If a name is passed use matplotlib's savefig command to save the
        plot to that file name. Otherwise, display the plot

    Returns
    -------
    Nothing
    """

    import matplotlib.pyplot as plt
    from desitarget.brightstar import ellipses

    #ADM initialize the default logger
    from desiutil.log import get_logger, DEBUG
    log = get_logger(DEBUG)

    start = time()

    #ADM set up the figure and the axis labels
    plt.figure(figsize=(8,8))
    plt.xlabel('RA (o)')
    plt.ylabel('Dec (o)')

    #ADM check if input objs is a filename or the actual data
    if isinstance(objs, str):
        objs = io.read_tractor(objs)

    #ADM coordinate limits and corresponding area of the passed objs
    ramin, ramax = np.min(objs["RA"]), np.max(objs["RA"])
    decmin, decmax = np.min(objs["DEC"]), np.max(objs["DEC"])
    #ADM guard against wraparound bug (which should never be an issue for the sweeps, anyway)
    if ramax - ramin > 180.:
        ramax -= 360.

    #ADM the avoidance separation (in arcseconds) for each object based on 
    #ADM its half-light radius/profile
    sep = calculate_separations(objs,navoid)
    #ADM the maximum such separation for any object in the passed set IN DEGREES
    maxrad = max(sep)/3600.

    #ADM limit the figure range based on the passed objs
    rarange, decrange = ramax - ramin, decmax - decmin
    rastep, decstep = rarange*0.47, decrange*0.47
    ralo, rahi = ramin+rastep, ramax-rastep
    declo, dechi = decmin+decstep, decmax-decstep
    plt.axis([ralo,rahi,declo,dechi])

    #ADM plot good and bad sky positions
    plt.scatter(ragood,decgood,marker='d',facecolors='none',edgecolors='k')
    plt.scatter(rabad,decbad,marker='s',facecolors='none',edgecolors='r')

    #ADM restrict the passed avoidance zones based on the passed limits
    #ADM remembering that we need to plot things at least the maximum/cos(~60o)
    #ADM times the possible avoidance zone beyond the plot limits
    w = np.where( (objs["RA"] > ralo-2*maxrad) & (objs["RA"] < rahi+2*maxrad) & 
                  (objs["DEC"] > declo-2*maxrad) & (objs["DEC"] < dechi+2*maxrad))
    
    log.info('Number of avoidance zones in plot area {}...t = {:.1f}s'.format(len(w[0]),time()-start))

    #ADM set up the ellipse shapes based on sizes of the past avoidance zones
    #ADM remembering to stretch by the COS term to de-project the sky
    minoraxis = sep/3600.
    majoraxis = minoraxis/np.cos(np.radians(objs["DEC"]))

    log.info('Plotting avoidance zones...t = {:.1f}s'.format(time()-start))
    #ADM plot the avoidance zones as ellipses
    out = ellipses(objs[w]["RA"], objs[w]["DEC"], 2*majoraxis[w], 2*minoraxis[w], alpha=0.2, edgecolor='none')

    #ADM display the plot, if requested
    if plotname is None:
        log.info('Displaying plot...t = {:.1f}s'.format(time()-start))
        plt.show()
    else:
        plt.savefig(plotname)    

    log.info('Done...t = {:.1f}s'.format(time()-start))

    return
