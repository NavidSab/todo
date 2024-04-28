<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Task extends Model
{
    use HasFactory;



    const STATUS_IN_PROGRESS = 'in_progress';
    const STATUS_COMPLETED = 'completed';

    protected $fillable = ['title', 'description', 'status', 'user_id'];

    public static function getStatusOptions()
    {
        return [
            self::STATUS_IN_PROGRESS => 'در حال انجام',
            self::STATUS_COMPLETED => 'انجام شده',
        ];
    }


    public function user()
    {
        return $this->belongsTo(User::class);
    }
}
