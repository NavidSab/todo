<?php

namespace App\Http\Controllers;

use App\Events\TaskStatusUpdated;
use Illuminate\Http\Request;
use App\Repositories\TaskRepository;
use Illuminate\Support\Facades\Auth;

class TaskController extends Controller
{
    protected $taskRepository;

    public function __construct(TaskRepository $taskRepository)
    {
        $this->taskRepository = $taskRepository;
    }

    public function store(Request $request)
    {
        $request->validate([
            'title' => 'required|string|max:255',
            'description' => 'nullable|string',
        ]);

        $task = $this->taskRepository->createTask(
            Auth::id(),
            $request->title,
            $request->description
        );

        return response()->json(['message' => 'Task created successfully', 'task' => $task]);
    }

 


      public function update(Request $request, $id)
    {
        $request->validate([
            'completed' => 'required|boolean',
        ]);

        $task = $this->taskRepository->updateStatus($id, $request->completed);

        //fire update task status change event
        event(new TaskStatusUpdated($task));

        return response()->json(['message' => 'Task status updated successfully', 'task' => $task]);


        
    }





}


