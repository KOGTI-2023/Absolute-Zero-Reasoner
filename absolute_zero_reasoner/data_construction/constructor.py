from typing import List, Dict

from numpy import random
import pandas as pd
from transformers import AutoTokenizer

from absolute_zero_reasoner.data_construction.prompts import get_code_problem_generator_prompt, get_code_problem_predictor_prompt
from absolute_zero_reasoner.data_construction.process_data import boxed_instruction, instruction_following
from absolute_zero_reasoner.utils.code_utils.parsers import replace_main_function_name


def get_gen_code_io_data(
    io_data: List[Dict],
    target_data_len: int,
    problem_type: str,
    instruction_type: str,
    content_max_length: int,
    io_n: int,
    output_path: str,
    split: str,
    tokenizer: AutoTokenizer,
    banned_keywords: List[str],
    banned_assertion_keywords: List[str],
    weights: List[float] = None,
    enable_composite_function: bool = False,
    composite_function_n_min: int = -1,
    composite_function_n_max: int = -1,
    composite_chance: float = 0.5,
    remove_after_return: bool = False,
    num_inputs: int = 10,
    remove_input_from_snippet: bool = False,
    include_references: bool = True,
):
    return_io_data = []
    if instruction_type.startswith('boxed'):
        instruction_template = boxed_instruction
    elif instruction_type.startswith('answer'):
        instruction_template = instruction_following
    elif instruction_type.startswith('none'):
        instruction_template = '{}'
    else:
        raise ValueError(f"Invalid instruction type: {instruction_type}")

    if weights is None:
        probabilities = [1.0 / len(io_data)] * len(io_data)
    else:
        # Normalize weights to form a probability distribution
        probabilities = [float(w)/sum(weights) for w in weights]
    
    idx = 0

    while len(return_io_data) < target_data_len:
        if not include_references and problem_type != 'code_f':
            chosen_references = []
        else:
            chosen_references = random.choice(io_data, size=min(io_n, len(io_data)), replace=False, p=probabilities)
        # composite functions is not used for code_f problem type
        if problem_type != 'code_f' and composite_function_n_max > 0 and enable_composite_function and random.random() <= composite_chance and len(chosen_references) > composite_function_n_max:
            # TODO: we only allow composite to sample from code snippets without composite functions
            io_without_composite_function_indices = [i for i in range(len(io_data)) if not io_data[i]['composite_functions']]
            io_without_composite_function_data = [io_data[i] for i in io_without_composite_function_indices]
            io_without_composite_function_weights = [probabilities[i] for i in io_without_composite_function_indices]
            # normalize the weights
            io_without_composite_function_probabilities = [w / sum(io_without_composite_function_weights) for w in io_without_composite_function_weights]
            # number of composite functions to sample is either fixed or random
            composite_function_n = composite_function_n_min if composite_function_n_min == composite_function_n_max else random.randint(composite_function_n_min, composite_function_n_max)
            composite_functions = random.choice(io_without_composite_function_data, size=composite_function_n, replace=False, p=io_without_composite_function_probabilities)
            for i, composite_function in enumerate(composite_functions):
                # TODO: need to also replace recursively called composite functions, ignore functions that have f as the last letter, only for function call f()
                composite_functions[i]['snippet'] = replace_main_function_name(composite_function['snippet'], 'f', f'g_{i}')
            imports = []
        else:
            composite_functions = []
            if include_references:
                imports = chosen_references[0]['imports']
            else:
                imports = []
        io_prompt = instruction_template.format(
            get_code_problem_generator_prompt(
                problem_type=problem_type,
                reference_snippets=chosen_references,
                banned_keywords=banned_keywords,
                banned_assertion_keywords=banned_assertion_keywords,
                composite_functions=composite_functions,
                remove_after_return=remove_after_return,
                num_inputs=num_inputs,
                remove_input_from_snippet=remove_input_from_snippet,
            )
        )
        if len(tokenizer(io_prompt)['input_ids']) <= content_max_length:
            io_item = {
                "data_source": 'gen_' + problem_type,
                "prompt": [{
                    "role": "user",
                    "content": io_prompt,
                }],
                "problem": '',
                "ability": "code",
                "reward_model": {
                    "style": "rule",
                    "ground_truth": '',
                },
                "extra_info": {
                    'split': split,
                    'index': idx,
                    'metric': 'gen_' + problem_type,
                    'chosen_references': chosen_references,
                    'composite_functions': composite_functions,
                    'imports': imports,
                }
            }
            return_io_data.append(io_item)
            idx += 1

        if len(return_io_data) >= target_data_len:
            break

    # if io_data is not full, we sample upsample random data
    while len(return_io_data) < target_data_len:
        io_item = io_data[random.randint(0, len(io_data))]
        return_io_data.append(io_item)

    # output to parquet
    df = pd.DataFrame(return_io_data)
    df.to_parquet(output_path)


def get_pred_code_io_data(
    io_data: List[Dict],
    target_data_len: int,
    problem_type: str,
    instruction_type: str,
    content_max_length: int,
    output_path: str,
    split: str,
    tokenizer: AutoTokenizer,
):
    return_io_data = []
    if instruction_type.startswith('boxed'):
        instruction_template = boxed_instruction
    elif instruction_type.startswith('answer'):
        instruction_template = instruction_following
    elif instruction_type.startswith('none'):
        instruction_template = '{}'
    else:
        raise ValueError(f"Invalid instruction type: {instruction_type}")

    for idx, io_item in enumerate(io_data):
        if problem_type == 'code_i':
            ground_truth = io_item['input']
        elif problem_type == 'code_o':
            ground_truth = io_item['output']
        elif problem_type == 'code_e':
            ground_truth = io_item['output']
        elif problem_type == 'code_f':
            ground_truth = io_item['snippet']
        else:
            raise ValueError(f"Invalid problem type: {problem_type}")
        if problem_type == 'code_f':
            num_given_inputs = len(io_item['inputs']) // 2
            num_given_outputs = len(io_item['outputs']) // 2
            given_inputs = list(io_item['inputs'][:num_given_inputs])
            given_outputs = list(io_item['outputs'][:num_given_outputs])
            hidden_inputs = list(io_item['inputs'][num_given_inputs:])
            hidden_outputs = list(io_item['outputs'][num_given_outputs:])
            io_prompt = instruction_template.format(
                get_code_problem_predictor_prompt(
                    problem_type=problem_type,
                    snippet=io_item['snippet'],
                    message=io_item['message'],
                    input_output_pairs=zip(given_inputs, given_outputs),
                )
            )
        else:
            io_prompt = instruction_template.format(
                get_code_problem_predictor_prompt(
                    problem_type=problem_type,
                    snippet=io_item['snippet'],
                    input_args=io_item['input'],
                    output=io_item['output'],
                )
            )
        if len(tokenizer(io_prompt)['input_ids']) <= content_max_length:
            output_io_item = {
                "data_source": 'pred_' + problem_type,
                "prompt": [{
                    "role": "user",
                    "content": io_prompt,
                }],
                "problem": io_item['snippet'],
                "ability": "code",
                "reward_model": {
                    "style": "rule",
                    "ground_truth": ground_truth,
                },
                "extra_info": {
                    'split': split,
                    'index': idx,
                    'metric': 'pred_' + problem_type,
                    'imports': io_item['imports'],
                }
            }
            if problem_type == 'code_f': # for code_f, we need to split the inputs and outputs into given and hidden, only show part of the inputs and outputs to the model
                output_io_item['extra_info']['given_inputs'] = given_inputs
                output_io_item['extra_info']['given_outputs'] = given_outputs
                output_io_item['extra_info']['hidden_inputs'] = hidden_inputs
                output_io_item['extra_info']['hidden_outputs'] = hidden_outputs
                output_io_item['extra_info']['message'] = io_item['message']
            else:
                output_io_item['extra_info']['input'] = io_item['input']
                output_io_item['extra_info']['output'] = io_item['output']
            return_io_data.append(output_io_item)

        if len(return_io_data) >= target_data_len:
            break

    # if io_data is not full, we sample upsample random data
    while len(return_io_data) < target_data_len:
        io_item = return_io_data[random.randint(0, len(return_io_data))]
        return_io_data.append(io_item)

    # output to parquet
    df = pd.DataFrame(return_io_data)
    df.to_parquet(output_path)

