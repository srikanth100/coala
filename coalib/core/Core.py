import asyncio
import concurrent.futures
import functools
import logging
import multiprocessing


def group(iterable, key=lambda x: x):
    """
    Groups elements (out-of-order) together in the given iterable.

    Supports non-hashable keys by comparing keys with ``==``.

    Accessing the groups is supported using the iterator as follows:

    >>> for key, elements in group([1, 3, 7, 1, 2, 1, 2]):
    ...     print(key, list(elements))
    1 [1, 1, 1]
    3 [3]
    7 [7]
    2 [2, 2]

    You can control how elements are grouped by using the ``key`` parameter. It
    takes a function with a single parameter and maps to the group.

    >>> data = [(1, 2), (3, 4), (1, 9), (2, 10), (1, 11), (7, 2), (10, 2),
    ...         (2, 1), (3, 7), (4, 5)]
    >>> for key, elements in group(data, key=sum):
    ...     print(key, list(elements))
    3 [(1, 2), (2, 1)]
    7 [(3, 4)]
    10 [(1, 9), (3, 7)]
    12 [(2, 10), (1, 11), (10, 2)]
    9 [(7, 2), (4, 5)]

    :param iterable:
        The iterable to group elements in.
    :param key:
        The key-function mapping an element to its group.
    :return:
        An iterable yielding tuples with ``key, elements``, where ``elements``
        is also an iterable yielding the elements grouped under ``key``.
    """
    keys = []
    elements = []

    for element in iterable:
        k = key(element)

        try:
            position = keys.index(k)
            element_list = elements[position]
        except ValueError:
            keys.append(k)
            element_list = []
            elements.append(element_list)

        element_list.append(element)

    return zip(keys, elements)


def get_cpu_count():
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:  # pragma: no cover
        # cpu_count is not implemented for some CPU architectures/OSes
        return 1


def cleanup_bear(bear,
                 result_callback,
                 running_tasks,
                 event_loop,
                 executor):
    """
    Cleans up state of an ongoing run for a bear.

    - If the given bear has no running tasks left, it removes the bear from
      the ``running_tasks`` dict.
    - Checks whether there are any remaining tasks, and quits the event loop
      accordingly if none are left.

    :param bear:
        The bear to clean up state for.
    :param result_callback:
        The result-callback handling results from bears.
    :param running_tasks:
        The dict of running-tasks.
    :param event_loop:
        The event-loop tasks are scheduled on.
    :param executor:
        The executor tasks are executed on.
    """
    if not running_tasks[bear]:
        del running_tasks[bear]

    if not running_tasks:
        event_loop.stop()


def schedule_bears(bears,
                   result_callback,
                   event_loop,
                   running_tasks,
                   executor):
    """
    Schedules the tasks of bears to the given executor and runs them on the
    given event loop.

    :param bears:
        A list of bear instances to be scheduled onto the process pool.
    :param result_callback:
        A callback function which is called when results are available.
    :param event_loop:
        The asyncio event loop to schedule bear tasks on.
    :param running_tasks:
        Tasks that are already scheduled, organized in a dict with
        bear instances as keys and asyncio-coroutines as values containing
        their scheduled tasks.
    :param executor:
        The executor to which the bear tasks are scheduled.
    """
    for bear in bears:
        tasks = {
            event_loop.run_in_executor(
                executor, bear.execute_task, bear_args, bear_kwargs)
            for bear_args, bear_kwargs in bear.generate_tasks()}

        running_tasks[bear] = tasks

        for task in tasks:
            task.add_done_callback(functools.partial(
                finish_task, bear, result_callback,
                running_tasks, event_loop, executor))

        logging.debug('Scheduled {!r} (tasks: {})'.format(bear,
                                                          len(tasks)))

        if not tasks:
            # We need to recheck our runtime if something is left to
            # process, as when no tasks were offloaded the event-loop could
            # hang up otherwise.
            cleanup_bear(bear, result_callback, running_tasks, event_loop,
                         executor)


def finish_task(bear,
                result_callback,
                running_tasks,
                event_loop,
                executor,
                task):
    """
    The callback for when a task of a bear completes. It is responsible for
    checking if the bear completed its execution and the handling of the
    result generated by the task.

    :param bear:
        The bear that the task belongs to.
    :param result_callback:
        A callback function which is called when results are available.
    :param running_tasks:
        Dictionary that keeps track of the remaining tasks of each bear.
    :param event_loop:
        The ``asyncio`` event loop bear-tasks are scheduled on.
    :param executor:
        The executor to which the bear tasks are scheduled.
    :param task:
        The task that completed.
    """
    try:
        results = task.result()
    except Exception as ex:
        # FIXME Try to display only the relevant traceback of the bear if error
        # FIXME occurred there, not the complete event-loop traceback.
        logging.error('An exception was thrown during bear execution.',
                      exc_info=ex)

        results = None
    finally:
        running_tasks[bear].remove(task)
        cleanup_bear(bear, result_callback, running_tasks, event_loop,
                     executor)

    if results is not None:
        for result in results:
            try:
                # FIXME Long operations on the result-callback could block the
                # FIXME   scheduler significantly. It should be possible to
                # FIXME   schedule new Python Threads on the given event_loop
                # FIXME   and process the callback there.
                result_callback(result)
            except Exception as ex:
                # FIXME Try to display only the relevant traceback of the
                # FIXME result handler if error occurred there, not the
                # FIXME complete event-loop traceback.
                logging.error(
                    'An exception was thrown during result-handling.',
                    exc_info=ex)


def run(bears, result_callback):
    """
    Runs a coala session.

    :param bears:
        The bear instances to run.
    :param result_callback:
        A callback function which is called when results are available. Must
        have following signature::

            def result_callback(result):
                pass
    """
    # FIXME Allow to pass different executors nicely, for example to execute
    # FIXME   coala with less cores, or to schedule jobs on distributed systems
    # FIXME   (for example Mesos).

    # Set up event loop and executor.
    event_loop = asyncio.SelectorEventLoop()
    executor = concurrent.futures.ProcessPoolExecutor(
        max_workers=get_cpu_count())

    # Let's go.
    schedule_bears(bears, result_callback, event_loop, {}, executor)
    try:
        event_loop.run_forever()
    finally:
        event_loop.close()
