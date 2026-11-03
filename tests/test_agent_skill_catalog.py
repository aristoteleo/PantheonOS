from pantheon.chatroom import skill_catalog
from pantheon.chatroom.app_models import AppSettings


def _settings(tmp_path, environment=None):
    return AppSettings(tmp_path / 'app', defaults={}, environment=environment or {})


def _skill(root, path, description, files=()):
    folder = root / path
    folder.mkdir(parents=True)
    name = path.rsplit('/', 1)[-1]
    (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {description}\n---\n# {name}\nSteps.\n')
    for file in files:
        (folder / file).parent.mkdir(parents=True, exist_ok=True)
        (folder / file).write_text('details')


def test_lists_the_skills_a_conversation_sees_without_one(tmp_path):
    settings = _settings(tmp_path)
    _skill(settings.global_skills_dir, 'mine/qc', 'My QC steps')
    _skill(settings.skills_dir, 'project-only', 'Project steps')
    listing = skill_catalog.list_skills(settings)
    assert listing['success']
    by_name = {s['name']: s for s in listing['skills']}
    assert by_name['mine/qc']['scope'] == 'global' and by_name['mine/qc']['description'] == 'My QC steps'
    assert by_name['project-only']['scope'] == 'project'
    # The skills shipped with the Agent are listed too.
    assert any(s['scope'] == 'factory' for s in listing['skills'])
    assert listing['count'] == len(listing['skills'])


def test_excluded_skills_stay_hidden(tmp_path):
    settings = _settings(tmp_path, {'PANTHEON_EXCLUDED_SKILLS': 'hidden'})
    _skill(settings.global_skills_dir, 'hidden', 'Not here')
    names = {s['name'] for s in skill_catalog.list_skills(settings)['skills']}
    assert 'hidden' not in names
    assert not skill_catalog.view_skill(settings, 'hidden')['success']


def test_view_reads_a_skill_and_its_files_and_says_what_to_run_next(tmp_path):
    settings = _settings(tmp_path)
    _skill(settings.global_skills_dir, 'mine/qc', 'My QC steps', files=['references/limits.md'])
    view = skill_catalog.view_skill(settings, 'mine/qc')
    assert view['success'] and 'Steps.' in view['content']
    assert view['files'] == ['references/limits.md']
    assert 'pantheon skills view mine/qc <file>' in view['hint']
    file = skill_catalog.view_skill(settings, 'mine/qc', 'references/limits.md')
    assert file['content'] == 'details'
    missing_file = skill_catalog.view_skill(settings, 'mine/qc', 'nope.md')
    assert not missing_file['success'] and 'references/limits.md' in missing_file['error']
    missing = skill_catalog.view_skill(settings, 'no-such-skill')
    assert not missing['success'] and 'pantheon skills list' in missing['error']
