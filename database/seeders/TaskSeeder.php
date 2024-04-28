<?php

namespace Database\Seeders;

use App\Models\Task;
use App\Models\User;
use Illuminate\Database\Seeder;

class TaskSeeder extends Seeder
{
    /**
     * Run the database seeds.
     */
    public function run(): void
    {
        $user = User::first();

        Task::create([
            'user_id' => $user->id,
            'title' => 'Complete Laravel project',
            'description' => 'Build a fully functional Laravel project',
            'status' => Task::STATUS_IN_PROGRESS //  وضعیت "در حال انجام"
        ]);

        Task::create([
            'user_id' => $user->id,
            'title' => 'Learn Vue.js',
            'description' => 'Study and practice Vue.js framework',
            'status' => Task::STATUS_IN_PROGRESS //  وضعیت "در حال انجام"
        ]);

        Task::create([
            'user_id' => $user->id,
            'title' => 'Write documentation',
            'description' => 'Document the project features and usage',
            'status' => Task::STATUS_COMPLETED //  وضعیت "انجام شده"
        ]);

        Task::create([
            'user_id' => $user->id,
            'title' => 'Test application',
            'description' => 'Perform testing and fix issues',
            'status' => Task::STATUS_COMPLETED //  وضعیت "انجام شده"
        ]);
    }
}

