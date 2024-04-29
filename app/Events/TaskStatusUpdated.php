<?php

namespace App\Events;

use App\Models\Task;
use Illuminate\Broadcasting\PrivateChannel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcast;

class TaskStatusUpdated implements ShouldBroadcast
{
    public $task;

    public function __construct(Task $task)
    {
        $this->task = $task;
    }

    public function broadcastOn()
    {
        return new PrivateChannel('task-status.' . $this->task->user_id);
    }

    public function broadcastAs()
    {
        return 'task.updated';
    }
}
