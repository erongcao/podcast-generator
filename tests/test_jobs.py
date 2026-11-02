"""进程内队列：一次一个，task_id 留在工作线程的 ContextVar 里。"""
import threading

from gui.web.jobs import current_task_id, enqueue, wait_idle


def test_jobs_run_one_at_a_time():
    order = []
    started = threading.Event()
    release = threading.Event()

    def job_a():
        order.append("a-start")
        assert current_task_id.get() == "a"
        started.set()
        assert release.wait(3), "测试卡住：第一个任务没有被放开"
        order.append("a-end")

    def job_b():
        order.append("b")
        assert current_task_id.get() == "b"

    enqueue(job_a, task_id="a")
    assert started.wait(2), "工作线程没有启动"
    ahead = enqueue(job_b, task_id="b")
    assert ahead >= 1
    assert order == ["a-start"]
    release.set()
    assert wait_idle(3)
    assert order == ["a-start", "a-end", "b"]
    assert current_task_id.get() is None
