""" Main experiments code """

import argparse
from importlib import import_module
import json
import os
import sys
from typing import Tuple, Any
import pathvalidate
import numpy as np
from math import ceil

from runner import collect_results
from caching import ResultsDescription


def get_searchspaces_info_stats() -> dict[str, Any]:
    """ read the searchspaces info statistics dictionary from file """
    with open("../cached_data_used/kernel_info.json") as file:
        kernels_device_info_data = file.read()
    return json.loads(kernels_device_info_data)


def change_directory(path: str):
    absolute_path = os.path.abspath(path)
    os.chdir(absolute_path)
    sys.path.append(absolute_path)


def get_experiment(filename: str) -> dict:
    """ Gets the experiment from the .json file """
    folder = 'experiments/'
    extension = '.json'
    if not filename.endswith(extension):
        filename = filename + extension
    path = filename
    if not filename.startswith(folder):
        path = folder + filename
    safe_path = pathvalidate.sanitize_filepath(path)
    with open(safe_path) as file:
        experiment = json.load(file)
        return experiment


def get_strategies(experiment: dict) -> dict:
    """ Gets the strategies from an experiments file by augmenting it with the defaults """
    strategy_defaults = experiment['strategy_defaults']
    strategies = experiment['strategies']
    # # get a baseline index if it exists
    # baseline_index = list(strategy_index for strategy_index, strategy in enumerate(strategies) if 'is_baseline' in strategy)
    # if len(baseline_index) != 1:
    #     raise ValueError(f"There must be exactly one baseline, found {len(baseline_index)} baselines")
    # if strategies[baseline_index[0]]['is_baseline'] != True:
    #     raise ValueError(f"is_baseline must be true, yet is set to {strategies[0]['is_baseline']}!")
    # # if the baseline index is not 0, put the baseline strategy first
    # if baseline_index[0] != 0:
    #     raise ValueError("The baseline strategy must be the first strategy in the experiments file!")
    #     # strategies.insert(0, strategies.pop(baseline_index[0]))

    # augment the strategies with the defaults
    for strategy in strategies:
        for default in strategy_defaults:
            if not default in strategy:
                strategy[default] = strategy_defaults[default]
    return strategies


def calc_cutoff_point(cutoff_percentile: float, stats_info) -> Tuple[float, int]:
    """ Calculate the cutoff point (objective value at cutoff point, fevals to cutoff point) """
    absolute_optimum: float = stats_info["absolute_optimum"]
    median: float = stats_info['median']
    inverted_sorted_times_arr = np.array(stats_info['sorted_times'])
    inverted_sorted_times_arr = inverted_sorted_times_arr[::-1]
    N = inverted_sorted_times_arr.shape[0]

    objective_value_at_cutoff_point = absolute_optimum + ((median - absolute_optimum) * (1 - cutoff_percentile))
    # fevals_to_cutoff_point = ceil((cutoff_percentile * N) / (1 + (1 - cutoff_percentile) * N))

    # i = next(x[0] for x in enumerate(inverted_sorted_times_arr) if x[1] > cutoff_percentile * arr[-1])
    i = next(x[0] for x in enumerate(inverted_sorted_times_arr) if x[1] <= objective_value_at_cutoff_point)
    # In case of x <= (1+p) * f_opt
    # i = next(x[0] for x in enumerate(inverted_sorted_times_arr) if x[1] <= (1 + (1 - cutoff_percentile)) * arr[-1])
    # In case of p*x <= f_opt
    # i = next(x[0] for x in enumerate(inverted_sorted_times_arr) if cutoff_percentile * x[1] <= arr[-1])
    fevals_to_cutoff_point = ceil(i / (N + 1 - i))
    return objective_value_at_cutoff_point, fevals_to_cutoff_point


def get_random_curve(cutoff_point_fevals: int, sorted_times: list, time_resolution: int = None) -> np.ndarray:
    """ Returns the values of the random curve at each function evaluation """
    dist = sorted_times
    ks = range(cutoff_point_fevals) if time_resolution is None else np.linspace(0, cutoff_point_fevals, time_resolution)

    def redwhite_index(dist, M):
        N = len(dist)
        # print("Running for subset size", M, end="\r", flush=True)
        #index = (N+1)*(N+1-M)*math.comb(N, M-1) / math.comb(N, M) / (M+1)
        index = M * (N + 1) / (M + 1)
        index = round(index)
        return dist[N - 1 - index]

    draws = np.array([redwhite_index(dist, k) for k in ks])
    return draws


def execute_experiment(filepath: str, profiling: bool, searchspaces_info_stats: dict) -> Tuple[dict, dict, dict]:
    """ Executes the experiment by retrieving it from the cache or running it """
    experiment = get_experiment(filepath)
    print(f"Starting experiment \'{experiment['name']}\'")
    kernel_path: str = experiment.get('kernel_path', "")
    minimization: bool = experiment.get('minimization', True)
    cutoff_quantile: float = experiment.get('cutoff_quantile', 0.975)
    cutoff_type: str = experiment.get('cutoff_type', "fevals")
    assert cutoff_type == 'fevals' or cutoff_type == 'time'
    curve_segment_factor: float = experiment.get('curve_segment_factor', 0.05)
    assert isinstance(curve_segment_factor, float)
    time_resolution: float = experiment.get('resolution', 1e4)
    if int(time_resolution) != time_resolution:
        raise ValueError(f"The resolution must be an integer, yet is {time_resolution}.")
    time_resolution = int(time_resolution)
    change_directory("../cached_data_used" + kernel_path)
    strategies = get_strategies(experiment)
    kernel_names = experiment['kernels']
    kernels = list(import_module(kernel_name) for kernel_name in kernel_names)

    # variables for comparison
    objective_time_keys = ['times']
    objective_value_key = 'time'
    objective_values_key = 'times'

    # execute each strategy in the experiment per GPU and kernel
    results_descriptions: dict[str, dict[str, dict[str, ResultsDescription]]] = dict()
    gpu_name: str
    for gpu_name in experiment['GPUs']:
        print(f" | running on GPU '{gpu_name}'")
        results_descriptions[gpu_name] = dict()
        for index, kernel in enumerate(kernels):
            kernel_name = kernel_names[index]
            stats_info = searchspaces_info_stats[gpu_name]['kernels'][kernel_name]

            # set cutoff point
            cutoff_point_value, cutoff_point_fevals = calc_cutoff_point(cutoff_quantile, stats_info)
            if cutoff_type == 'time':
                mean: float = stats_info['mean']
                repeats: int = stats_info['repeats']
                mean_feval_time = (mean * repeats) / 1000    # in seconds # TODO change this to the new specified output format in kernel_info_generator
                cutoff_point_time = cutoff_point_fevals * mean_feval_time

            print(f" | - optimizing kernel '{kernel_name}'")
            results_descriptions[gpu_name][kernel_name] = dict()
            for strategy in strategies:
                strategy_name = strategy['name']
                strategy_display_name = strategy['display_name']
                stochastic = strategy['stochastic']
                print(f" | - | using strategy '{strategy['display_name']}'")

                # setup the results description
                if not 'options' in strategy:
                    strategy['options'] = dict()
                limit_margin = 1.1    # 10% margin for non-valid evaluations
                if cutoff_type == 'time':
                    strategy['options']['time_limit'] = cutoff_point_time * limit_margin
                else:
                    strategy['options']['max_fevals'] = cutoff_point_fevals * limit_margin
                results_description = ResultsDescription(kernel_name, gpu_name, strategy_name, strategy_display_name, stochastic, objective_time_keys,
                                                         objective_value_key, objective_values_key)

                # if the strategy is in the cache, use cached data
                if 'ignore_cache' not in strategy and results_description.has_results():
                    print(" | - |-> retrieved from cache")
                else:    # execute each strategy that is not in the cache
                    results_description = collect_results(kernel, strategy, results_description, profiling=profiling, minimization=minimization,
                                                          error_value=1e20)

                # set the results
                results_descriptions[gpu_name][kernel_name][strategy_name] = results_description

    return experiment, strategies, results_descriptions


if __name__ == "__main__":
    CLI = argparse.ArgumentParser()
    CLI.add_argument("experiment", type=str, help="The experiment.json to execute, see experiments/template.json")
    args = CLI.parse_args()
    experiment_filepath = args.experiment
    if experiment_filepath is None:
        raise ValueError("Invalid '-experiment' option. Run 'experiments.py -h' to read more about the options.")

    execute_experiment(experiment_filepath, profiling=False, searchspaces_info_stats=get_searchspaces_info_stats())
