<?php

namespace App\Repositories;

use App\Models\Task;

class TaskRepository
{
    public function createTask($userId, $title, $description)
    {
        $task = new Task();
        $task->user_id = $userId;
        $task->title = $title;
        $task->description = $description;
        $task->completed = false;
        $task->save();

        return $task;
    }

    public function updateStatus($taskId, $completed)
    {
        $task = Task::findOrFail($taskId);
        $task->completed = $completed;
        $task->save();
        return $task;
    }
}
