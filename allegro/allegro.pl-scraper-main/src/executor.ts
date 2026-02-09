import 'dotenv/config';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { config } from '@/config';
import type { TaskResponse } from '@/types';

interface TaskTracker {
    taskId: string;
    ean: string;
}

async function main(): Promise<void> {
    console.log('🚀 Allegro Batch Executor\n');

    // Read EANs from file
    const eansPath = config.EANS_FILE;
    if (!existsSync(eansPath)) {
        console.error(`❌ EANs file not found: ${eansPath}`);
        process.exit(1);
    }

    const allEans = readFileSync(eansPath, 'utf-8')
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0 && /^\d{8,13}$/.test(line));

    if (allEans.length === 0) {
        console.error('❌ No valid EANs found in file');
        process.exit(1);
    }

    console.log(`📋 Loaded ${String(allEans.length)} EANs from ${eansPath}`);

    // Create output directory
    const outputDir = config.OUTPUT_DIR;
    if (!existsSync(outputDir)) {
        mkdirSync(outputDir, { recursive: true });
        console.log(`📁 Created output directory: ${outputDir}`);
    }

    // Filter out EANs that already have output files
    const eansToProcess = allEans.filter((ean) => {
        const outputPath = join(outputDir, `${ean}.json`);
        return !existsSync(outputPath);
    });

    const skipped = allEans.length - eansToProcess.length;
    if (skipped > 0) {
        console.log(`⏭️  Skipping ${String(skipped)} EANs (already have output files)`);
    }

    if (eansToProcess.length === 0) {
        console.log('\n✅ All EANs already processed!');
        process.exit(0);
    }

    console.log(`\n🚀 Submitting ${String(eansToProcess.length)} tasks...`);

    // Submit all tasks
    const pending: TaskTracker[] = [];
    const baseUrl = config.API_BASE_URL;

    for (const ean of eansToProcess) {
        try {
            const res = await fetch(`${baseUrl}/createTask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ean }),
            });

            if (!res.ok) {
                console.error(`❌ Failed to submit EAN ${ean}: ${res.statusText}`);
                continue;
            }

            const data = (await res.json()) as { taskId: string };
            pending.push({ taskId: data.taskId, ean });
        } catch (err) {
            console.error(`❌ Failed to submit EAN ${ean}:`, err);
        }
    }

    console.log(`✓ Submitted ${String(pending.length)} tasks\n`);

    if (pending.length === 0) {
        console.error('❌ No tasks were submitted successfully');
        process.exit(1);
    }

    // Poll for results
    let completed = 0;
    let failed = 0;
    const startTime = Date.now();

    while (pending.length > 0) {
        const stillPending: TaskTracker[] = [];

        for (const tracker of pending) {
            try {
                const res = await fetch(`${baseUrl}/getTaskResult/${tracker.taskId}`);

                if (!res.ok) {
                    stillPending.push(tracker);
                    continue;
                }

                const data = (await res.json()) as TaskResponse;

                if (data.status === 'completed' && data.result) {
                    const outputPath = join(outputDir, `${tracker.ean}.json`);
                    writeFileSync(outputPath, JSON.stringify(data.result, null, 2));
                    completed++;
                    console.log(`✓ ${tracker.ean} → ${outputPath}`);
                } else if (data.status === 'failed') {
                    failed++;
                    console.log(`✗ ${tracker.ean} → ${data.error ?? 'Unknown error'}`);
                } else {
                    // Still pending or processing
                    stillPending.push(tracker);
                }
            } catch {
                stillPending.push(tracker);
            }
        }

        pending.length = 0;
        pending.push(...stillPending);

        if (pending.length > 0) {
            const total = completed + failed + pending.length;
            const progress = Math.round(((completed + failed) / total) * 100);
            process.stdout.write(`\r⏳ Processing... ${String(progress)}% (${String(completed)} done, ${String(failed)} failed, ${String(pending.length)} remaining)   `);
            await sleep(config.POLL_INTERVAL);
        }
    }

    // Final summary
    const duration = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log('\n\n📊 Summary:');
    console.log(`   Completed: ${String(completed)}`);
    console.log(`   Failed: ${String(failed)}`);
    console.log(`   Skipped: ${String(skipped)}`);
    console.log(`   Duration: ${duration}s`);
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((err) => {
    console.error('Fatal error:', err);
    process.exit(1);
});
