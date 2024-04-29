<?php

namespace App\Http\Requests;

use App\Models\Task;
use Illuminate\Foundation\Http\FormRequest;

class TaskStatusUpdateRequest extends FormRequest
{
    public function authorize()
    {
        return true; // Authorization logic (if needed)
    }

    public function rules()
    {
        return [
            'status' => 'required|string|in:' . implode(',', [
                Task::STATUS_IN_PROGRESS,
                Task::STATUS_COMPLETED,
            ]),
        ];

    }

    public function messages()
    {
        return [
            'status.required' => 'وضعیت تسک الزامی است.',
            'status.in' => 'وضعیت تسک نامعتبر است.',
        ];

    }

    public function failedValidation($validator)
    {
        $response = response()->json([
            'message' => 'Validation failed',
            'errors' => $validator->errors(),
        ], 422); // 422 Unprocessable Entity

        throw new \Illuminate\Validation\ValidationException($validator, $response);
    }
}
