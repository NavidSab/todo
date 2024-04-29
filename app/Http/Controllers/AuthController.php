<?php

namespace App\Http\Controllers;

use App\Repositories\UserRepository;
use App\Http\Requests\UserRegistrationRequest;
use App\Http\Requests\UserLoginRequest;
use Illuminate\Support\Facades\Auth;

class AuthController extends Controller
{
    protected $userRepository;

    public function __construct(UserRepository $userRepository)
    {

        $this->userRepository = $userRepository;
    }

    public function register(UserRegistrationRequest $request)
    {

        $userData = $request->validated();
        $user = $this->userRepository->createUser($userData);
        return response()->json(['message' => 'User registered successfully', 'user' => $user]);
    }

    public function login(UserLoginRequest $request)
    {
        $credentials = $request->validated();

        $user = $this->userRepository->getUserByEmail($credentials['email']);

        if (!$user || !Auth::attempt($credentials)) {
            return response()->json(['message' => 'Unauthorized'], 401);
        }

        $token = $user->createToken('authToken')->plainTextToken;

        return response()->json(['token' => $token]);
    }
}
