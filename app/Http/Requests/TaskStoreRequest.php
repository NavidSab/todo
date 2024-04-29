<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class TaskStoreRequest extends FormRequest
{
    public function authorize()
    {
        return true; // Authorization logic (if needed)
    }

    public function rules()
    {
        return [
            'title' => 'required|string|max:255',
            'description' => 'nullable|string',
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
