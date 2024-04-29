<?php

namespace App\Http\Requests;


use Illuminate\Foundation\Http\FormRequest;

class UserRegistrationRequest extends FormRequest
{
    public function authorize()
    {
        return true; // Allow all users to access this endpoint (authorization logic can be added here if needed)
    }

    public function rules()
    {

        return [
            'name' => 'required|string|max:255',
            'email' => 'required|string|email|max:255|unique:users',
            'password' => 'required|string|min:6',
        ];
    }
    public function messages()
    {
        return [
            'email.unique' => 'The email address has already been taken.',
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
