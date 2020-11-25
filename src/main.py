#!/usr/bin/env python3
"""Scientific discovery
"""

import argparse
import time
import os
from os.path import join as pjoin
import inspect

import sys
import numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import random, igraph, scipy
import scipy.optimize
from myutils import info, create_readme, append_to_file
import pandas as pd
from multiprocessing import Pool
from itertools import product

#############################################################
NONE = 0
NUCLEUS = 1
RESOURCE = 2

#############################################################
UNIFORM = 0
DEGREE = 1
BETWV = 2
CLUCOEFF = 3
CLUCOEFF2 = 4

#############################################################
def generate_graph(model, nvertices, avgdegree, rewiringprob,
                   latticethoroidal=False, tmpdir='/tmp/'):
    """Generate graph with given topology """
    # info(inspect.stack()[0][3] + '()')

    if model == 'la':
        mapside = int(np.sqrt(nvertices))
        g = igraph.Graph.Lattice([mapside, mapside], nei=1, circular=latticethoroidal)
    elif model == 'er':
        erdosprob = avgdegree / nvertices
        if erdosprob > 1: erdosprob = 1
        g = igraph.Graph.Erdos_Renyi(nvertices, erdosprob)
    elif model == 'ba':
        m = int(round(avgdegree/2))
        if m == 0: m = 1
        g = igraph.Graph.Barabasi(nvertices, m)
    elif model == 'gr':
        radius = get_rgg_params(nvertices, avgdegree)
        g = igraph.Graph.GRG(nvertices, radius)
    else:
        msg = 'Please choose a proper topology model'
        raise Exception(msg)

    if model in ['gr', 'wx']:
        aux = np.array([ [g.vs['x'][i], g.vs['y'][i]] for i in range(g.vcount()) ])
        # layoutmodel = 'grid'
    else:
        if model in ['la']:
            layoutmodel = 'grid'
        else:
            layoutmodel = 'random'
        aux = np.array(g.layout(layoutmodel).coords)
    # coords = (aux - np.mean(aux, 0))/np.std(aux, 0) # standardization
    coords = -1 + 2*(aux - np.min(aux, 0))/(np.max(aux, 0)-np.min(aux, 0)) # minmax
    g.vs['type'] = NONE
    g['coords'] = coords
    return g

##########################################################
def weighted_random_sampling(items, weights, return_idx=False):
    n = len(items)
    cumsum = np.cumsum(weights)
    cumsumnorm = cumsum / cumsum[-1]
    x = np.random.rand()

    for i in range(n):
        if x < cumsumnorm[i]:
            if return_idx: return i
            else: return items[i]

    info('Something wrong x:{}'.format(x))

    if return_idx: return -1
    else: return items[-1]

##########################################################
def weighted_random_sampling_n(items, weights, n):
    item = items.copy(); weights = weights.copy()
    sample = np.zeros(n, dtype=int)
    inds = list(range(len(items)))

    for i in range(n):
        sampleidx = weighted_random_sampling(items, weights, return_idx=True)
        sample[i] = items[sampleidx]
        items = np.delete(items, sampleidx)
        weights = np.delete(weights, sampleidx)

    return sample

##########################################################
def calculate_modified_clucoeff(g):
    """Clustering coefficient as proposed by Luc"""
    # info(inspect.stack()[0][3] + '()')
    adj = np.array(g.get_adjacency().data)

    mult = np.matmul(adj, adj)

    for i in range(mult.shape[0]): mult[i, i] = 0 # Ignoring reaching self

    clucoeffs = np.zeros(g.vcount(), dtype=float)
    for i in range(g.vcount()):
        neighs1 = np.where(adj[i, :] > 0)[0].tolist()
        neighs2 = np.where(mult[i, :] > 0)[0].tolist()
        neighs = list(set([i] + neighs1 + neighs2))
        n = len(neighs)
        induced = adj[neighs, :][:, neighs]
        m = np.sum(induced) / 2
        if n == 1: continue
        clucoeffs[i] = m / ( n * (n-1) / 2)

    return clucoeffs

##########################################################
def add_labels(gorig, n, choice, label):
    """Add @nresources to the @g.
    We randomly sample the vertices and change their labels"""
    # info(inspect.stack()[0][3] + '()')
    g = gorig.copy()
    types = np.array(g.vs['type'])

    noneinds = np.where(types == NONE)[0]
    validinds = noneinds

    if choice == UNIFORM:
        weights = np.ones(len(noneinds), dtype=float)
    elif choice == DEGREE:
        degrs = np.array(g.degree())
        weights = degrs[noneinds]
    elif choice == BETWV:
        betwvs = np.array(g.betweenness())
        weights = betwvs[noneinds]
    elif choice == CLUCOEFF:
        clucoeffs = np.array(g.transitivity_local_undirected())
        valid = np.argwhere(~np.isnan(clucoeffs)).flatten()
        validinds = list(set(valid).intersection(set(noneinds)))
        weights = clucoeffs[validinds]
    elif choice == CLUCOEFF2:
        clucoeffs = calculate_modified_clucoeff(g)
        valid = np.argwhere(~np.isnan(clucoeffs)).flatten()
        validinds = list(set(valid).intersection(set(noneinds)))
        weights = clucoeffs[validinds]
    else: info('Invalid choice!')

    sample = weighted_random_sampling_n(validinds, weights, n)

    for i in range(n):
        idx = sample[i]
        g.vs[idx]['type'] = label
    return g, sorted(sample[:n])

##########################################################
def get_rgg_params(nvertices, avgdegree):
    rggcatalog = {
        '625,6': 0.056865545,
        '10000,6': 0.0139,
        '11132,6': 0.0131495,
        '22500,6': 0.00925,
         '1000,6': 0.044389839846333226,
        '1000,20': 0.08276843878986143,
       '1000,100': 0.19425867981373568
    }

    if '{},{}'.format(nvertices, avgdegree) in rggcatalog.keys():
        return rggcatalog['{},{}'.format(nvertices, avgdegree)]

    def f(r):
        g = igraph.Graph.GRG(nvertices, r)
        return np.mean(g.degree()) - avgdegree

    return scipy.optimize.brentq(f, 0.0001, 10000)

##########################################################
def run_experiment(params):
    """Single run, given a model. nvertices, avgdegree, nucleiratio, and niter"""
    model = params['model']
    nvertices = params['nvertices']
    avgdegree = params['avgdegree']
    nucleipref = params['nucleipref']
    nucleistep = params['nucleistep']
    iter = params['iter']

    rewiringprob = .5

    info('{},{},{},{},{:.02f},{}'.format(model, nvertices, avgdegree, nucleipref,
                                 nucleistep, iter))

    nucleiratios = np.arange(nucleistep, 1.0, nucleistep) # np.arange(0, 1.01, .05)
    ret = [[model, nvertices, avgdegree, nucleipref, 0.0, iter, 0, 1.0]]

    rprev1 = 0
    rprev2 = 0

    for c in nucleiratios:
        nnuclei = int(c * nvertices)
        neighs = []

        nresources = nvertices - nnuclei
        g = generate_graph(model, nvertices, avgdegree, rewiringprob)
        g, nuclids = add_labels(g, nnuclei, nucleipref, NUCLEUS)
        g, resoids = add_labels(g, nresources, UNIFORM, RESOURCE)

        for nucl in nuclids:
            neighids = np.array(g.neighbors(nucl))
            neightypes = np.array(g.vs[neighids.tolist()]['type'])
            neighresoids = np.where(neightypes == RESOURCE)[0]
            neighs.extend(neighids[neighresoids])

        lenunique = len(set(neighs))
        lenrepeated = len(neighs)

        r = lenunique / nvertices
        s = lenunique / lenrepeated if lenunique > 0 else 0
        ret.append([model, nvertices, avgdegree, nucleipref, c, iter, r, s])
        if r < rprev2 and rprev1 < rprev2:
            break
        else:
            rprev2 = rprev1
            rprev1 = r

    if len(ret) > 2: return ret[:-2]
    else: return ret[:-1]
    return ret

##########################################################
def main():
    info(inspect.stack()[0][3] + '()')
    t0 = time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', default=0, type=int, help='Random seed')
    parser.add_argument('--nprocs', default=1, type=int, help='Number of parallel processes')
    parser.add_argument('--outdir', default='/tmp/out/', help='Output directory')
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    readmepath = create_readme(sys.argv, args.outdir)

    np.random.seed(args.seed)
    random.seed(args.seed)

    models = ['ba', 'er', 'gr'] # ['er', 'ba', 'gr']
    nvertices = [100, 500, 1000] # [100, 500, 1000]
    avgdegrees = np.arange(4, 21) # np.arange(4, 21)
    nucleiprefs = [UNIFORM, DEGREE] # [UNIFORM, DEGREE]
    nucleistep = .01
    niter = 3

    append_to_file(readmepath, 'models:{}'.format(models))
    append_to_file(readmepath, 'nvertices:{}'.format(nvertices))
    append_to_file(readmepath, 'avgdegrees:{}'.format(avgdegrees))
    append_to_file(readmepath, 'nucleiprefs:{}'.format(nucleiprefs))
    append_to_file(readmepath, 'nucleistep:{}'.format(nucleistep))
    append_to_file(readmepath, 'niter:{}'.format(niter))

    aux = list(product(models, nvertices, avgdegrees, nucleiprefs, [nucleistep],
                       list(range(niter)))) # Fill here
    params = []
    for i, row in enumerate(aux):
        params.append(dict(model = row[0],
                           nvertices = row[1],
                           avgdegree = row[2],
                           nucleipref = row[3],
                           nucleistep = row[4],
                           iter = row[5],
                           ))

    if args.nprocs == 1:
        info('Running serially (nprocs:{})'.format(args.nprocs))
        ret = [run_experiment(p) for p in params]
    else:
        info('Running in parallel (nprocs:{})'.format(args.nprocs))
        pool = Pool(args.nprocs)
        ret = pool.map(run_experiment, params)

    df = pd.DataFrame()
    cols = ['model', 'nvertices', 'k', 'nucleipref', 'c', 'i', 'r', 's']

    res = []
    for blob in ret: res.extend(blob)

    for i, col in enumerate(cols):
        df[col] = [x[i] for x in res]

    respath = pjoin(args.outdir, 'results.csv')
    df.to_csv(respath, index=False)
    info('Elapsed time:{}'.format(time.time()-t0))
    info('Output generated in {}'.format(args.outdir))

##########################################################
if __name__ == "__main__":
    main()
