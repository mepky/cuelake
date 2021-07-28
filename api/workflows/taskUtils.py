from typing import List
import os
import time
import logging
import datetime as dt
import polling
from workflows.models import (
    WorkflowRun,
    WorkflowNotebookMap,
    STATUS_SUCCESS,
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_ABORTED
)
from utils.zeppelinAPI import Zeppelin

from genie.tasks import runNotebookJob as runNotebookJobTask
from genie.models import NOTEBOOK_STATUS_QUEUED, NotebookRunLogs, NOTEBOOK_STATUS_RUNNING, NOTEBOOK_STATUS_SUCCESS

# Get an instance of a logger
logger = logging.getLogger(__name__)

# Name of the celery task which runs the notebook job
CELERY_TASK_NAME = "genie.tasks.runNotebookJob"
NOTEBOOK_BATCH_COUNT = os.environ.get("NOTEBOOK_BATCH_COUNT", 10)

class TaskUtils:
    """
    Class containing workflow job utils
    """
    @staticmethod
    def runWorkflow(workflowId: int, taskId: str, workflowRunId: int = None):
        """
        Runs workflow
        """
        notebookIds = TaskUtils.__getNotebookIdsInWorkflow(workflowId)
        workflowRun = TaskUtils.__getOrCreateWorkflowRun(workflowId, taskId, workflowRunId)
        notebookRunLogsIds = TaskUtils.__runNotebookJobsFromList(notebookIds, workflowRun.id)
        workflowStatus = polling.poll(
            lambda: TaskUtils.__checkGivenRunStatuses(notebookRunLogsIds),
            check_success= lambda x: x != "RUNNING",
            step=3,
            timeout=3600*6,
        )
        logger.info(f"Workflow status : {str(workflowStatus)}")

        if WorkflowRun.objects.get(id=workflowRun.id).status == STATUS_ABORTED:
            return STATUS_ABORTED

        workflowRun.status = STATUS_SUCCESS if workflowStatus else STATUS_ERROR
        workflowRun.endTimestamp = dt.datetime.now()
        workflowRun.save()
        return workflowRun.status

    @staticmethod
    def __runNotebookJobsFromList(notebookIds: List[int], workflowRunId: int):
        """
        Runs notebook jobs for all notebookIds
        """
        notebookRunLogsIds = []
        for notebookId in notebookIds:
            notebookRunLogs = NotebookRunLogs.objects.create(
                notebookId=notebookId, status=NOTEBOOK_STATUS_QUEUED, runType="Workflow", workflowRun_id=workflowRunId
            )
            response = runNotebookJobTask.delay(notebookId=notebookId, notebookRunLogsId=notebookRunLogs.id)
            notebookRunLogs.taskId = response.id
            notebookRunLogs.save()
            notebookRunLogsIds.append(notebookRunLogs.id)
            time.sleep(0.2) # Sleep for 200ms to make sure zeppelin server has been allocated to previous notebook
        return notebookRunLogsIds
    
    @staticmethod
    def __getNotebookIdsInWorkflow(workflowId: int):
        """
        Returns list of notebook ids in a workflow
        """
        notebookIds = list(
            WorkflowNotebookMap.objects.filter(workflow_id=workflowId).values_list(
                "notebookId", flat=True
            )
        )
        return notebookIds

    @staticmethod
    def __getOrCreateWorkflowRun(workflowId: int, taskId: str, workflowRunId: int = None):
        """
        Gets or Creates workflow run object
        """
        if workflowRunId:
            workflowRun = WorkflowRun.objects.get(id=workflowRunId)
            workflowRun.status = STATUS_RUNNING
            workflowRun.taskId = taskId
            workflowRun.save()
        else:
            workflowRun = WorkflowRun.objects.create(
                workflow_id=workflowId, status=STATUS_RUNNING, taskId=taskId
            )
        return workflowRun
    
    @staticmethod
    def __checkGivenRunStatuses(notebookRunLogsIds: List[int]):
        """
        Check if given runStatuses are status is SUCCESS
        """
        runningAndQueuedNotebookCount = NotebookRunLogs.objects.filter(id__in=notebookRunLogsIds).exclude(status=NOTEBOOK_STATUS_RUNNING).exclude(status=NOTEBOOK_STATUS_QUEUED).count()
        if (len(notebookRunLogsIds) == runningAndQueuedNotebookCount):
            successfulNotebookCount = NotebookRunLogs.objects.filter(id__in=notebookRunLogsIds, status=NOTEBOOK_STATUS_SUCCESS).count()
            logger.info(f"Batch completed. Successfull Notebooks : {str(successfulNotebookCount)}. Notebooks in batch: {str(len(notebookRunLogsIds))}")
            logger.info(f"Notebook Run Status Ids: {str(notebookRunLogsIds)}")
            return (len(notebookRunLogsIds) == successfulNotebookCount)
        return "RUNNING"
