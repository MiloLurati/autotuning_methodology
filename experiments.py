""" Main experiments code """

import argparse
from importlib import import_module
import json
from jsonschema import validate
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
    """ Validates and gets the experiment from the .json file """
    folder = 'experiment_files/'
    extension = '.json'
    if not filename.endswith(extension):
        filename = filename + extension
    path = filename
    if not filename.startswith(folder):
        path = folder + filename
    safe_path = pathvalidate.sanitize_filepath(path)
    schemafilepath = folder + 'schema.json'
    with open(safe_path) as file, open(schemafilepath) as schemafile:
        schema = json.load(schemafile)
        experiment = json.load(file)
        validate(instance=experiment, schema=schema)
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


def median_time_per_feval(stats_info: dict) -> float:
    """ Median time in seconds per function evaluation """
    median: float = stats_info['median']
    repeats: int = stats_info['repeats']
    median_feval_time = (median * repeats) / 1000    # in seconds # TODO change this to the new specified output format in kernel_info_generator
    return median_feval_time


def calc_cutoff_point(cutoff_percentile: float, stats_info: dict) -> Tuple[float, int]:
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


def calc_cutoff_point_fevals_time(cutoff_percentile: float, stats_info: dict) -> Tuple[float, int, float]:
    """ Calculate the cutoff point (objective value at cutoff point, fevals to cutoff point, mean time to cutoff point) """
    cutoff_point_value, cutoff_point_fevals = calc_cutoff_point(cutoff_percentile, stats_info)
    cutoff_point_time = cutoff_point_fevals * median_time_per_feval(stats_info)
    return cutoff_point_value, cutoff_point_fevals, cutoff_point_time


def execute_experiment(filepath: str, profiling: bool, searchspaces_info_stats: dict) -> Tuple[dict, dict, dict]:
    """ Executes the experiment by retrieving it from the cache or running it """
    experiment = get_experiment(filepath)
    print(f"Starting experiment \'{experiment['name']}\'")
    experiment_folder_id = experiment.get('folder_id')
    kernel_path: str = experiment.get('kernel_path', "")
    minimization: bool = experiment.get('minimization', True)
    cutoff_percentile: float = experiment.get('cutoff_percentile', 1)
    cutoff_type: str = experiment.get('cutoff_type', "fevals")
    assert cutoff_type == 'fevals' or cutoff_type == 'time'
    curve_segment_factor: float = experiment.get('curve_segment_factor', 0.05)
    assert isinstance(curve_segment_factor, float)
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
            _, cutoff_point_fevals, cutoff_point_time = calc_cutoff_point_fevals_time(cutoff_percentile, stats_info)

            print(f" | - optimizing kernel '{kernel_name}'")
            results_descriptions[gpu_name][kernel_name] = dict()
            for strategy in strategies:
                strategy_name: str = strategy['name']
                strategy_display_name: str = strategy['display_name']
                stochastic = strategy['stochastic']
                print(f" | - | using strategy '{strategy['display_name']}'")

                # setup the results description
                if not 'options' in strategy:
                    strategy['options'] = dict()
                cutoff_margin = 2.0    # +10% margin, to make sure cutoff_point is reached by compensating for potential non-valid evaluations

                # TODO make sure this works correctly (but how could it?)
                # if cutoff_type == 'time':
                #     strategy['options']['time_limit'] = cutoff_point_time * cutoff_margin
                # else:
                strategy['options']['max_fevals'] = int(ceil(cutoff_point_fevals * cutoff_margin))
                results_description = ResultsDescription(experiment_folder_id, kernel_name, gpu_name, strategy_name, strategy_display_name, stochastic,
                                                         objective_time_keys, objective_value_key, objective_values_key, minimization)

                # if the strategy is in the cache, use cached data
                if 'ignore_cache' not in strategy and results_description.has_results():
                    print(" | - |-> retrieved from cache")
                else:    # execute each strategy that is not in the cache
                    results_description = collect_results(kernel, strategy, results_description, profiling=profiling, error_value=1e20)

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
