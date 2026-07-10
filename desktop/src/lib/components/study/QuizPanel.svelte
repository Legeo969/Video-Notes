<script lang="ts">
  import Icon from "../Icon.svelte";
  import { engineCall } from "../../api";
  import type { QuizQuestion } from "../../types";

  let { noteId, onError }: { noteId: number; onError: (msg: string) => void } = $props();

  let questions = $state<QuizQuestion[]>([]);
  let currentIndex = $state(0);
  let selectedAnswer = $state<number | null>(null);
  let answered = $state(false);
  let score = $state(0);
  let loading = $state(false);
  let finished = $state(false);

  async function generateQuiz() {
    loading = true;
    try {
      const result = await engineCall<QuizQuestion[]>("study.quiz", { note_id: noteId });
      questions = result;
      currentIndex = 0;
      selectedAnswer = null;
      answered = false;
      score = 0;
      finished = false;
    } catch (e) {
      onError(String(e));
    } finally {
      loading = false;
    }
  }

  function selectAnswer(i: number) {
    if (!answered) selectedAnswer = i;
  }

  function submitAnswer() {
    if (selectedAnswer === null) return;
    answered = true;
    if (selectedAnswer === questions[currentIndex].correctIndex) {
      score++;
      score = score;
    }
  }

  function nextQuestion() {
    if (currentIndex < questions.length - 1) {
      currentIndex++;
      selectedAnswer = null;
      answered = false;
    } else {
      finished = true;
    }
  }

  function prevQuestion() {
    if (currentIndex > 0) {
      currentIndex--;
      selectedAnswer = null;
      answered = false;
    }
  }

  function restartQuiz() {
    currentIndex = 0;
    selectedAnswer = null;
    answered = false;
    score = 0;
    finished = false;
  }
</script>

<div class="quiz-panel">
  {#if loading}
    <div class="quiz-loading"><span class="loading-ring"></span><p>正在生成测验题目…</p></div>
  {:else if finished}
    <div class="quiz-result">
      <div class="result-score">{score} / {questions.length}</div>
      <p class="result-label">测验完成</p>
      <p class="result-detail">
        {#if score === questions.length}
          全部正确！你对内容掌握得非常好。
        {:else if score >= questions.length * 0.6}
          表现不错，继续巩固薄弱环节。
        {:else}
          建议回顾知识图谱后重新测验。
        {/if}
      </p>
      <button class="btn btn-primary" onclick={restartQuiz}>再来一次</button>
    </div>
  {:else if questions.length === 0}
    <div class="quiz-empty">
      <p>根据此笔记生成测验题，检验理解程度。</p>
      <button class="btn btn-primary" onclick={generateQuiz} disabled={loading}>
        <Icon name="sparkles" size={15} />生成测验
      </button>
    </div>
  {:else}
    <div class="quiz-header">
      <span class="quiz-progress">第 {currentIndex + 1} / {questions.length} 题</span>
      <span class="quiz-score">得分：{score} / {questions.length}</span>
    </div>
    <div class="quiz-question">
      <strong>{questions[currentIndex].question}</strong>
    </div>
    <div class="quiz-choices">
      {#each questions[currentIndex].choices as choice, i}
        <button class="quiz-choice" class:selected={selectedAnswer === i} class:correct={answered && i === questions[currentIndex].correctIndex} class:wrong={answered && selectedAnswer === i && i !== questions[currentIndex].correctIndex} onclick={() => selectAnswer(i)} disabled={answered}>
          <span class="choice-key">{String.fromCharCode(65 + i)}</span>
          <span>{choice}</span>
        </button>
      {/each}
    </div>
    {#if answered}
      <div class="quiz-explanation">
        <Icon name="info" size={15} />
        <span>{questions[currentIndex].explanation}</span>
      </div>
      <div class="quiz-nav">
        <button class="btn btn-secondary" onclick={prevQuestion} disabled={currentIndex === 0}>上一题</button>
        <button class="btn btn-primary" onclick={nextQuestion}>{currentIndex < questions.length - 1 ? "下一题" : "查看结果"}</button>
      </div>
    {:else}
      <button class="btn btn-primary" onclick={submitAnswer} disabled={selectedAnswer === null}>提交答案</button>
    {/if}
  {/if}
</div>

<style>
  .quiz-panel { display: flex; flex-direction: column; gap: 14px; }

  .quiz-loading, .quiz-empty, .quiz-result {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 32px 20px;
    text-align: center;
    gap: 10px;
  }
  .quiz-loading p, .quiz-empty p, .quiz-result p {
    color: var(--text-secondary);
    font-size: 13px;
  }
  .quiz-loading .loading-ring {
    width: 28px; height: 28px;
    border: 3px solid var(--bg-progress);
    border-top-color: var(--accent-color);
    border-radius: 50%;
    animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .quiz-result .result-score {
    font-size: 36px;
    font-weight: 780;
    color: var(--accent-color);
    letter-spacing: -.03em;
    line-height: 1;
  }
  .quiz-result .result-label {
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .quiz-result .result-detail {
    max-width: 280px;
    color: var(--text-secondary);
    font-size: 13px;
    line-height: 1.6;
  }

  .quiz-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-color);
  }
  .quiz-progress {
    font-size: 12px;
    font-weight: 650;
    color: var(--text-tertiary);
  }
  .quiz-score {
    font-size: 12px;
    font-weight: 680;
    color: var(--accent-color);
  }

  .quiz-question {
    font-size: 14px;
    line-height: 1.6;
    color: var(--text-primary);
  }

  .quiz-choices {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .quiz-choice {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    background: var(--bg-card);
    color: var(--text-primary);
    font-size: 13px;
    line-height: 1.5;
    text-align: left;
    cursor: pointer;
    transition: border-color .14s, background .14s, box-shadow .14s;
  }
  .quiz-choice:hover:not(:disabled) {
    background: var(--bg-hover);
    border-color: var(--border-strong);
  }
  .quiz-choice.selected {
    border-color: var(--accent-color);
    background: var(--accent-faint);
    box-shadow: inset 3px 0 0 var(--accent-color);
  }
  .quiz-choice.correct {
    border-color: var(--success-color);
    background: var(--success-soft);
    color: var(--success-color);
    box-shadow: inset 3px 0 0 var(--success-color);
  }
  .quiz-choice.wrong {
    border-color: var(--danger-color);
    background: var(--danger-soft);
    color: var(--danger-color);
    box-shadow: inset 3px 0 0 var(--danger-color);
  }
  .quiz-choice:disabled { cursor: default; opacity: 1; }
  .choice-key {
    display: grid;
    place-items: center;
    width: 24px;
    height: 24px;
    flex: 0 0 auto;
    border-radius: 6px;
    background: var(--bg-muted);
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 700;
  }
  .quiz-choice.correct .choice-key { background: color-mix(in srgb, var(--success-color) 16%, transparent); color: var(--success-color); }
  .quiz-choice.wrong .choice-key { background: color-mix(in srgb, var(--danger-color) 16%, transparent); color: var(--danger-color); }

  .quiz-explanation {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--info-soft);
    color: var(--info-color);
    font-size: 13px;
    line-height: 1.6;
  }

  .quiz-nav {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
</style>
