import dotenv from 'dotenv';
import process from 'node:process';

dotenv.config({
    quiet: true,
});

export const getEnv = (key: string) => {
    const value = process.env[key];
    if (!value) {
        throw new Error(`Environment variable ${key} is not set`);
    }
    return value;
};
