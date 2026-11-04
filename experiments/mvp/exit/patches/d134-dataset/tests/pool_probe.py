"""Fast spawn safety fixture: different finish order, worker fault, restored env."""
import json
import os
from pathlib import Path
import time
import llm.exit
llm.exit.__path__.insert(0,str(Path(__file__).resolve().parents[1]))
from llm.exit.dataset_pool import ordered_pool


def delayed(value):
    assert os.environ['OMP_NUM_THREADS']=='1'
    assert os.environ['STOWAGE_IN_PORT_WORKERS']=='1'
    time.sleep((4-value)*.005)
    return value


def fails(value):
    if value==2:raise ValueError('fixture worker violation')
    return value


if __name__=='__main__':
    os.environ['OMP_NUM_THREADS']='7'
    with ordered_pool(2) as work:
        order=work(delayed,[1,2,3,4])
        try:
            work(fails,[1,2,3])
        except ValueError as error:
            failure=str(error)
        else:raise AssertionError('worker fault swallowed')
    assert os.environ['OMP_NUM_THREADS']=='7'
    print(json.dumps(dict(order=order,failure=failure,environment_restored=True)))
