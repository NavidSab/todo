<?php

namespace Tests\Feature;

use Tests\TestCase;
use App\Models\User;
use App\Models\Task;
use App\Repositories\TaskRepository;
use App\Http\Requests\TaskStoreRequest;
use App\Http\Controllers\TaskController;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;

class TaskControllerTest extends TestCase
{
    use RefreshDatabase;

    protected $taskRepository;
    protected $user;

    protected function setUp(): void
    {
        parent::setUp();
        $this->user = User::factory()->create();
        Auth::shouldReceive('id')->andReturn($this->user->id);
    }

    public function testStoreMethod()
    {
        $requestData = [
            'user_id'=> 1,
            'title' => 'Test Task',
            'description' => 'This is a test task',
        ];

        $response = $this->postJson('/api/tasks', $requestData);


        $response->assertStatus(200)
            ->assertJson([
                'message' => 'Task created successfully'
            ]);
    }
}
