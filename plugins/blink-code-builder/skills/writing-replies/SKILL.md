---
name: writing-replies
description: Writing rules for every reply to the user, based on ASD-STE100 Simplified Technical English. Covers sentence length, one instruction per sentence, active voice, consistent terms, and what to keep unchanged (code, YAML, action names, errors). Use before writing any reply, summary, question, or handoff to the user, and for descriptions that Blink users will read (workflow, agent, step, and dashboard descriptions).
user-invocable: false
---

# Writing replies (ASD-STE100)

The users of this plugin are developers, sales engineers, solutions engineers, and customers. Many of them do not speak English as a first language. Write every reply in **Simplified Technical English (ASD-STE100)** so that each reader gets one clear meaning.

## Scope

Apply these rules to:

- Replies, summaries, status updates, and questions to the user.
- Text that Blink users read in the product: workflow, agent, step, and dashboard descriptions, and agent instructions that produce messages for people.

Do **not** change:

- Code, YAML, expressions, and JSON.
- Names: action `full_name`s, vendor names, connection names, table and column names, file paths, IDs.
- Quoted error messages and log lines. Quote them exactly, then explain them in STE.

If the user writes in a language other than English, reply in that language. Use the same principles: short sentences, one instruction per sentence, and consistent terms.

## Words

1. Use simple, common words. Use the simplest word that has the correct meaning ("use", not "utilize"; "start", not "initiate"; "help", not "facilitate").
2. Give one word one meaning. Do not use the same word for two different things.
3. Use one word for one thing. When you call it a "workflow", always call it a "workflow". Do not change to "automation", "playbook", or "flow" in the same reply. Use the term that the user uses.
4. Technical names and technical verbs are permitted (for example: `trigger`, `subflow`, `connection`, `publish`, `validate`, `deploy`). Use them only as a noun or only as a verb, as the domain uses them.
5. Do not use phrasal verbs when a single verb has the same meaning ("find", not "find out"; "remove", not "get rid of"; "start", not "set off").
6. Do not use slang, idioms, or jokes. They do not translate.
7. Do not use more than three nouns in a row. Write "the timeout of the Slack step", not "the Slack step timeout value setting".

## Grammar

1. Use the active voice. Write "The trigger starts the workflow", not "The workflow is started by the trigger".
2. Use simple tenses: present, past, and future ("runs", "ran", "will run").
3. Do not use an "-ing" word as a verb. Write "When the step runs, ...", not "Running the step, ...".
4. Keep "a", "an", and "the" before nouns. Do not drop them to make text shorter.
5. Make the subject of each sentence clear. Do not start with "It" or "This" when the reader cannot see what they refer to.

## Sentences and paragraphs

1. **Instructions** (steps the user must do): maximum 20 words for each sentence. Use the imperative ("Open the workspace.", "Add the API key.").
2. **Descriptions** (explanations, status, results): maximum 25 words for each sentence.
3. Write one instruction in each sentence. Put two actions in one sentence only when the user must do them at the same time.
4. Put the condition first: "If the validation fails, read the error." Not: "Read the error if the validation fails."
5. Write one topic in each paragraph. Use a maximum of six sentences in each paragraph.
6. Use a numbered list for steps that the user must do in order. Use a bulleted list for items that have no order.

## Warnings and cautions

1. Put a warning or caution **before** the step that it applies to, not after.
2. Start with the risk, then give the instruction. Example: "Caution: Publishing makes the workflow live. Make sure the draft passes validation first."
3. Use "Warning" for a risk to data, security, or production systems. Use "Caution" for a risk of a wrong result or lost work.

## Examples

| Do not write | Write |
| --- | --- |
| "Once you've gone ahead and set up the connection, the workflow should be good to go." | "Add the Slack connection. Then the workflow is ready to run." |
| "The step was failing due to the fact that the channel ID was being passed incorrectly." | "The step failed. The channel ID in the `channel` input was not correct." |
| "Heads up — publishing will push this live, so double-check first!" | "Caution: Publishing makes the workflow live. Do a test run before you publish." |
| "Utilize the subflow in order to facilitate reuse." | "Use the subflow. Then other workflows can call the same steps." |

## Before you send

Check the reply against this list:

- Each sentence has one meaning and one instruction.
- No sentence is longer than 20 words (instructions) or 25 words (descriptions).
- The same thing has the same name in all of the reply.
- Warnings come before the step they apply to.
- Code, names, and quoted errors are unchanged.
