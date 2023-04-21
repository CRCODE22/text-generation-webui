import os

import torch
import torchaudio
import sys
sys.path.append(os.path.join(sys.path[0], 'tortoise'))

sys.path.append(os.path.join(os.path.dirname(__file__), 'tortoise'))
try:
    from tortoise import api
    from tortoise.models import utils
    from tortoise.utils import audio
    from tortoise.utils.text import split_and_recombine_text
except ImportError:
    from .tortoise.tortoise import api
    from .tortoise.tortoise.models import utils
    from .tortoise.tortoise.utils import audio
    from .tortoise.tortoise.utils.text import split_and_recombine_text

from pathlib import Path
import time

from modules import tts_preprocessor

params = {
    'activate': True,
    'voice_dir': None,
    'model_dir': None,
    'output_dir': None,
    'voice': 'emma',
    'preset': 'single_sample',
    'low_vram': True,
    'model_swap': False,
    'seed': 0,
    'sentence_length': 100,
    'show_text': True,
    'autoplay': True,
    'tuning_settings': {
        'k': 1,
        'num_autoregressive_samples': 0,
        'temperature': 0,
        'length_penalty': 0,
        'repetition_penalty': 0,
        'top_p': 0,
        'max_mel_tokens': 0,
        'cvvp_amount': 0,
        'diffusion_iterations': 0,
        'cond_free': None,
        'cond_free_k': 0,
        'diffusion_temperature': 0,
        'sampler': None
    }
}

# Presets are defined here.
preset_options = {
    "single_sample": {"num_autoregressive_samples": 8, "diffusion_iterations": 10, "sampler": "ddim"},
    "ultra_fast": {"num_autoregressive_samples": 16, "diffusion_iterations": 10, "sampler": "ddim"},
    "ultra_fast_old": {"num_autoregressive_samples": 16, "diffusion_iterations": 30, "cond_free": False},
    "very_fast": {"num_autoregressive_samples": 32, "diffusion_iterations": 30, "sampler": "dpm++2m"},
    "fast": {"num_autoregressive_samples": 96, "diffusion_iterations": 20, "sampler": "dpm++2m"},
    "fast_old": {"num_autoregressive_samples": 96, "diffusion_iterations": 80},
    "standard": {"num_autoregressive_samples": 256, "diffusion_iterations": 200},
    "high_quality": {"num_autoregressive_samples": 256, "diffusion_iterations": 400},
}
presets = preset_options.keys()


def set_preset(preset):
    global params
    settings = get_preset_settings(preset)
    for opt in settings.keys():
        option = params['tuning_settings'][opt]
        if option == settings[opt]:
            continue

        if isinstance(option, bool) and option is not None:
            continue

        if isinstance(option, str) and option is not None and option != '':
            continue

        if isinstance(option, (int, float)) and option > 0:
            continue

        params['tuning_settings'][opt] = settings[opt]


def get_preset_settings(preset):
    settings = {
        "temperature": 0.2, "length_penalty": 1.0, "repetition_penalty": 2.0, "top_p": 0.8, "cond_free_k": 2.0,
        "diffusion_temperature": 1.0, 'num_autoregressive_samples': 512, 'max_mel_tokens': 500, 'cvvp_amount': 0,
        'diffusion_iterations': 100, 'cond_free': True, 'sampler': 'ddim'
    }

    settings.update(preset_options[preset])
    return settings


def get_gen_kwargs(par):
    gen_kwargs = {
        'preset': par['preset'],
        'use_deterministic_seed': int(time.time()) if par['seed'] is None or par['seed'] == 0 else par['seed'],
        'k': 1
    }

    preset_options = get_preset_settings(par['preset'])

    for option in par['tuning_settings'].keys():
        opt: [float | int | str | bool | None] = par['tuning_settings'][option]
        if opt is None:
            continue

        if isinstance(opt, (int, float)) and opt <= 0:
            continue

        if isinstance(opt, str) and opt == '':
            continue

        if option in preset_options.keys() and preset_options[option] == opt:
            continue

        gen_kwargs[option] = opt

    return gen_kwargs


def get_voices():
    extra_voice_dirs = [params['voice_dir']] if params['voice_dir'] is not None and Path(params['voice_dir']).is_dir() else []
    detected_voices = audio.get_voices(extra_voice_dirs=extra_voice_dirs)
    detected_voices = sorted(detected_voices.keys()) if len(detected_voices) > 0 else []
    return detected_voices


def load_model():
    # Init TTS
    try:
        global params
        extra_voice_dirs = [params['voice_dir']] if params['voice_dir'] is not None else []
        if not Path(params['model_dir']).is_dir():
            Path(params['model_dir']).mkdir(parents=True, exist_ok=True)

        utils.MODELS_DIR = os.path.join(params['model_dir'], 'tortoise')

        tts = api.TextToSpeech(high_vram=not params['low_vram'], models_dir=utils.MODELS_DIR)
        samples, latents = audio.load_voice(voice=params['voice'], extra_voice_dirs=extra_voice_dirs)
    except Exception as e:
        print(e)
        return None, None, None

    return tts, samples, latents


voices = get_voices()
set_preset(params['preset'])
model, voice_samples, conditioning_latents = load_model()


def output_modifier(string):
    """
    This function is applied to the model outputs.
    """

    try:
        global model, voice_samples, conditioning_latents, params

        refresh_model = False

        if model is None:
            refresh_model = True

        if refresh_model:
            model, voice_samples, conditioning_latents = load_model()

        if model is None:
            return string

        original_string = string
        # we don't need to handle numbers. The text normalizer in tortoise does it better
        string = tts_preprocessor.replace_invalid_chars(string)
        string = tts_preprocessor.replace_abbreviations(string)
        string = tts_preprocessor.clean_whitespace(string)

        if string == '':
            string = '*Empty reply, try regenerating*'
            print(string)

        out_dir_root = params['output_dir'] if params['output_dir'] is not None and Path(params['output_dir']).is_dir() \
            else 'extensions/tortoise_tts_fast/outputs'

        output_dir = Path(out_dir_root).joinpath('parts')
        if not output_dir.is_dir():
            output_dir.mkdir(parents=True, exist_ok=True)

        output_file = Path(out_dir_root).joinpath(f'test_{int(time.time())}.wav')

        if '|' in string:
            texts = string.split('|')
        else:
            texts = split_and_recombine_text(string, desired_length=params['sentence_length'], max_length=1000)

        gen_kwargs = get_gen_kwargs(params)

        generate_audio(model, voice_samples, conditioning_latents, output_dir, output_file, gen_kwargs, texts)

        autoplay = 'autoplay' if params['autoplay'] else ''
        string = f'<audio src="file/{output_file.as_posix()}" controls {autoplay}></audio>'
        if params['show_text']:
            string += f'\n\n{original_string}'

        print(string)
    except Exception as e:
        print(e)


def generate_audio(tts, samples, latents, output_dir, output_file, gen_kwargs, texts):
    # only cat if it's needed
    if len(texts) <= 0:
        return

    if len(texts) == 1:
        text = texts[0]
        gen = tts.tts_with_preset(text, voice_samples=samples, conditioning_latents=latents, **gen_kwargs)
        gen = gen.squeeze(0).cpu()
        torchaudio.save(str(output_file), gen, 24000)
        return

    all_parts = []
    for j, text in enumerate(texts):
        gen = tts.tts_with_preset(text, voice_samples=samples, conditioning_latents=latents, **gen_kwargs)
        gen = gen.squeeze(0).cpu()
        torchaudio.save(str(output_dir.joinpath(f'{j}_{int(time.time())}.wav')), gen, 24000)
        all_parts.append(gen)

    full_audio = torch.cat(all_parts, dim=-1)
    torchaudio.save(str(output_file), full_audio, 24000)


if __name__ == '__main__':
    import sys
    output_modifier(sys.argv[1])
