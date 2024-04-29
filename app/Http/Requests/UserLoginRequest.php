<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class UserLoginRequest extends FormRequest
{
    public function authorize()
    {
        return true; // Allow all users to access this endpoint
    }

    public function rules()
    {
        return [
            'email' => 'required|string|email',
            'password' => 'required|string',
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

