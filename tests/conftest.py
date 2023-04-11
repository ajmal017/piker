from contextlib import asynccontextmanager as acm
from functools import partial
import logging
import os
from pathlib import Path

import pytest
import pytest_trio
import tractor
from piker import (
    config,
)
from piker.service import (
    Services,
)
from piker.log import get_console_log


def pytest_addoption(parser):
    parser.addoption("--ll", action="store", dest='loglevel',
                     default=None, help="logging level to set when testing")
    parser.addoption("--confdir", default=None,
                     help="Use a practice API account")


@pytest.fixture(scope='session')
def loglevel(request) -> str:
    return request.config.option.loglevel


@pytest.fixture(scope='session')
def test_config():
    dirname = os.path.dirname
    dirpath = os.path.abspath(
        os.path.join(
            dirname(os.path.realpath(__file__)),
            'data'
        )
    )
    return dirpath


@pytest.fixture(scope='session', autouse=True)
def confdir(
    request,
    test_config: str,
):
    '''
    If the `--confdir` flag is not passed use the
    broker config file found in that dir.

    '''
    confdir = request.config.option.confdir
    if confdir is not None:
        config._override_config_dir(confdir)

    return confdir


_ci_env: bool = os.environ.get('CI', False)


@pytest.fixture(scope='session')
def ci_env() -> bool:
    '''
    Detect CI envoirment.

    '''
    return _ci_env


@pytest.fixture()
def log(
    request: pytest.FixtureRequest,
    loglevel: str,
) -> logging.Logger:
    '''
    Deliver a per-test-named ``piker.log`` instance.

    '''
    return get_console_log(
        level=loglevel,
        name=request.node.name,
    )


@acm
async def _open_test_pikerd(
    tmpconfdir: str,

    reg_addr: tuple[str, int] | None = None,
    loglevel: str = 'warning',
    debug_mode: bool = False,

    **kwargs,

) -> tuple[
    str,
    int,
    tractor.Portal
]:
    '''
    Testing helper to startup the service tree and runtime on
    a different port then the default to allow testing alongside
    a running stack.

    '''
    import random
    from piker.service import maybe_open_pikerd

    if reg_addr is None:
        port = random.randint(6e3, 7e3)
        reg_addr = ('127.0.0.1', port)

    async with (
        maybe_open_pikerd(
            registry_addr=reg_addr,
            loglevel=loglevel,

            tractor_runtime_overrides={
                'piker_test_dir': tmpconfdir,
            },

            # tests may need to spawn containers dynamically
            # or just in sequence per test, so we keep root.
            drop_root_perms_for_ahab=False,

            debug_mode=debug_mode,

            **kwargs,

        ) as service_manager,
    ):
        # this proc/actor is the pikerd
        assert service_manager is Services

        async with tractor.wait_for_actor(
            'pikerd',
            arbiter_sockaddr=reg_addr,
        ) as portal:
            raddr = portal.channel.raddr
            assert raddr == reg_addr
            yield (
                raddr[0],
                raddr[1],
                portal,
                service_manager,
            )


@pytest.fixture
def open_test_pikerd(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    loglevel: str,
):
    tmpconfdir: Path = tmp_path / '_testing'
    tmpconfdir.mkdir()
    tmpconfdir_str: str = str(tmpconfdir)

    # override config dir in the root actor (aka
    # this top level testing process).
    from piker import config
    config._config_dir = tmpconfdir

    # NOTE: on linux the tmp config dir is generally located at:
    # /tmp/pytest-of-<username>/pytest-<run#>/test_<current_test_name>/
    # the default `pytest` config ensures that only the last 4 test
    # suite run's dirs will be persisted, otherwise they are removed:
    # https://docs.pytest.org/en/6.2.x/tmpdir.html#the-default-base-temporary-directory
    print(f'CURRENT TEST CONF DIR: {tmpconfdir}')

    yield partial(
        _open_test_pikerd,

        # pass in a unique temp dir for this test request
        # so that we can have multiple tests running (maybe in parallel)
        # bwitout clobbering each other's config state.
        tmpconfdir=tmpconfdir_str,

        # bind in level from fixture, which is itself set by
        # `--ll <value>` cli flag.
        loglevel=loglevel,

        debug_mode=request.config.option.usepdb
    )

    # NOTE: the `tmp_dir` fixture will wipe any files older then 3 test
    # sessions by default:
    # https://docs.pytest.org/en/6.2.x/tmpdir.html#the-default-base-temporary-directory
    # BUT, if we wanted to always wipe conf dir and all contained files,
    # rmtree(str(tmp_path))

    # TODO: teardown checks such as,
    # - no leaked subprocs or shm buffers
    # - all requested container service are torn down
    # - certain ``tractor`` runtime state?
