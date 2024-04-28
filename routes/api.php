<?php
use App\Http\Controllers\TaskController;
use App\Http\Controllers\AuthController;


Route::post('/register', [AuthController::class, 'register']);

Route::post('/login', [AuthController::class, 'login']);

Route::middleware('auth:sanctum')->group(function () {
    //store  task
    Route::post('/tasks', [TaskController::class, 'store']);
    //update status of task
    Route::put('/tasks/{id}', [TaskController::class, 'update']);
});


