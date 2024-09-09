import asyncio

from mango.container.core import Container


def unfinished_task_count(container: Container):
    unfinished_tasks = 0
    for agent in container._agents.values():
        sleeping_tasks = []
        scheduled_tasks = (
            agent._scheduler._scheduled_tasks
            + agent._scheduler._scheduled_process_tasks
        )
        for scheduled_task, task, _, _ in scheduled_tasks:
            if scheduled_task._is_sleeping.done():
                # we need to recognize how many sleeping tasks we have in order to find out if all tasks are done
                sleeping_tasks.append(scheduled_task)
        unfinished_tasks += len(scheduled_tasks) - len(sleeping_tasks)
    return unfinished_tasks


async def tasks_complete_or_sleeping(container: Container, except_sources=["no_wait"]):
    sleeping_tasks = []
    task_list = []
    await container.inbox.join()
    # python does not have do while pattern
    for agent in container._agents.values():
        await agent.inbox.join()
        task_list.extend(agent._scheduler._scheduled_tasks)
        task_list.extend(agent._scheduler._scheduled_process_tasks)

    task_list = list(filter(lambda x: x[3] not in except_sources, task_list))
    while len(task_list) > len(sleeping_tasks):
        # sleep needed so that asyncio tasks of this time step are correctly awaken.
        # await asyncio.sleep(0)
        await container.inbox.join()
        for scheduled_task, task, _, _ in task_list:
            await asyncio.wait(
                [scheduled_task._is_sleeping, scheduled_task._is_done],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if (
                scheduled_task._is_sleeping.done()
                and scheduled_task not in sleeping_tasks
            ):
                # we need to recognize how many sleeping tasks we have in order to find out if all tasks are done
                sleeping_tasks.append(scheduled_task)

        # recreate task_list - as new tasks might have been added
        task_list = []
        for agent in container._agents.values():
            await agent.inbox.join()
            task_list.extend(agent._scheduler._scheduled_tasks)
            task_list.extend(agent._scheduler._scheduled_process_tasks)
        task_list = list(filter(lambda x: x[3] not in except_sources, task_list))
