<?php

namespace App\Console\Commands;

use App\Models\Task;
use Carbon\Carbon;
use Illuminate\Console\Command;

class MarkOverdueTasksCompleted extends Command
{
    protected $signature = 'tasks:mark-overdue-completed';
    protected $description = 'Mark overdue tasks as completed';

    public function __construct()
    {
        parent::__construct();
    }

    public function handle()
    {
        $tasks = Task::where('status', 'in_progress')
            ->whereDate('created_at', '<=', Carbon::now()->subDays(2)->toDateString())
            ->get();

        foreach ($tasks as $task) {
            $task->status = 'completed'; // Mark task as completed
            $task->save();
        }

        $this->info('Overdue tasks marked as completed successfully.');
    }
}