<?php

namespace App\Http\Controllers;

use App\Events\TaskStatusUpdated;
use App\Repositories\TaskRepository;
use Illuminate\Support\Facades\Auth;
use App\Http\Requests\TaskStoreRequest;
use App\Http\Requests\TaskStatusUpdateRequest;

class TaskController extends Controller
{
    protected $taskRepository;

    public function __construct(TaskRepository $taskRepository)
    {
        $this->taskRepository = $taskRepository;
        
    }

    public function store(TaskStoreRequest $request)
    {

        $validatedData = $request->validated();

        $task = $this->taskRepository->createTask(
            Auth::id(),
            $validatedData['title'],
            $validatedData['description']
        );

        return response()->json(['message' => 'Task created successfully', 'task' => $task]);
    }

    public function update(TaskStatusUpdateRequest $request, $id)
    {
        $validatedData = $request->validated();

        try {
            $task = $this->taskRepository->updateStatus($id, $validatedData['status']);

            // Fire update task status change event
            event(new TaskStatusUpdated($task));

            return response()->json(['message' => 'Task status updated successfully', 'task' => $task]);
        } catch (\Exception $e) {
            // Log or handle the exception appropriately
            return response()->json(['message' => 'Failed to update task status', 'error' => $e->getMessage()], 500);
        }
    }
}


