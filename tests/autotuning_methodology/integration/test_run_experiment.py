import json
from pathlib import Path

import pytest
from jsonschema import validate

from autotuning_methodology.experiments import (
    ResultsDescription,
    execute_experiment,
    get_args_from_cli,
    get_experiment_schema_filepath,
)

# setup file paths
mockfiles_path_root = Path("tests/autotuning_methodology/integration/mockfiles/")
mockfiles_path_source = mockfiles_path_root / "mock_gpu.json"
mockfiles_path = Path("..") / mockfiles_path_root
cached_visualization_path = Path("cached_data_used/visualizations/test_run_experiment/mocktest_kernel_convolution")
cached_visualization_file = cached_visualization_path / "mock_GPU_random_sample_10_iter.npz"
normal_cachefiles_path = Path("cached_data_used/cachefiles/mocktest_kernel_convolution")
normal_cachefile_destination = normal_cachefiles_path / "mock_gpu.json"


def _remove_dir(path: Path):
    assert path.exists()
    for sub in path.iterdir():
        sub.unlink()
    path.rmdir()


def setup_module():
    # create files where necessary
    assert mockfiles_path_source.exists()
    normal_cachefiles_path.mkdir(parents=True, exist_ok=True)
    assert normal_cachefiles_path.exists()
    normal_cachefile_destination.write_text(mockfiles_path_source.read_text())
    assert normal_cachefile_destination.exists()
    cached_visualization_path.mkdir(parents=True, exist_ok=True)
    assert cached_visualization_path.exists()


def teardown_module():
    # remove files where necessary
    if normal_cachefile_destination.exists():
        normal_cachefile_destination.unlink()
    _remove_dir(normal_cachefiles_path)
    if cached_visualization_file.exists():
        cached_visualization_file.unlink()
    _remove_dir(cached_visualization_path)


def test_CLI_input():
    """Test bad and good CLI inputs"""

    # improper input 1
    with pytest.raises(SystemExit) as e:
        dummy_args = ["-dummy_arg=option"]
        get_args_from_cli(dummy_args)
    assert e.type == SystemExit
    assert e.value.code == 2

    # improper input 2
    with pytest.raises(ValueError, match="Invalid '-experiment' option"):
        get_args_from_cli([""])

    # proper input
    args = get_args_from_cli(["bogus_filename"])
    assert args == "bogus_filename"


def test_bad_experiment():
    """Attempting to run a non-existing experiment file should raise a clear error"""
    experiment_filepath = "bogus_filename"
    with pytest.raises(AssertionError, match=" does not exist, attempted path: "):
        execute_experiment(experiment_filepath, profiling=False)

    experiment_filepath = "experiment_files/bogus_filename"
    with pytest.raises(AssertionError, match=" does not exist, attempted path: "):
        execute_experiment(experiment_filepath, profiling=False)


def test_run_experiment_bad_kernel_path():
    """Run an experiment with a bad kernel path"""
    experiment_filepath = str(mockfiles_path / "test_bad_kernel_path.json")
    with pytest.raises(FileNotFoundError, match="No such path"):
        execute_experiment(experiment_filepath, profiling=False)


@pytest.fixture(scope="session")
def test_run_experiment():
    """Run a dummy experiment"""
    assert normal_cachefile_destination.exists()
    if cached_visualization_file.exists():
        cached_visualization_file.unlink()
    assert not cached_visualization_file.exists()
    experiment_filepath = str(mockfiles_path / "test.json")
    (experiment, strategies, results_descriptions) = execute_experiment(experiment_filepath, profiling=False)
    validate_experiment_results(experiment, strategies, results_descriptions)


@pytest.mark.usefixtures("test_run_experiment")
def test_cached_experiment():
    """Retrieve a cached experiment"""
    assert normal_cachefiles_path.exists()
    assert normal_cachefile_destination.exists()
    assert cached_visualization_path.exists()
    assert cached_visualization_file.exists()
    experiment_filepath = str(mockfiles_path / "test.json")
    (experiment, strategies, results_descriptions) = execute_experiment(experiment_filepath, profiling=False)
    validate_experiment_results(experiment, strategies, results_descriptions)


def validate_experiment_results(
    experiment,
    strategies,
    results_descriptions,
):
    # validate the types
    assert isinstance(experiment, dict)
    assert isinstance(strategies, list)
    assert isinstance(results_descriptions, dict)

    # validate the contents
    schemafilepath = get_experiment_schema_filepath()
    with open(schemafilepath) as schemafile:
        schema = json.load(schemafile)
        validate(instance=experiment, schema=schema)
    kernel_name = experiment["kernels"][0]
    gpu_name = experiment["GPUs"][0]
    assert len(strategies) == 1
    strategy_name = strategies[0]["name"]
    assert isinstance(results_descriptions[gpu_name][kernel_name][strategy_name], ResultsDescription)
    assert isinstance(results_descriptions[gpu_name][kernel_name][strategy_name], ResultsDescription)