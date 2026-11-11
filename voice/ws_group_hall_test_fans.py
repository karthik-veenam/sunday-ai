"""Give each Hall Test fan its own light control panel."""
import copy
import ws_align_hall_test_climate as runner


def group_fans(config):
    cards = config['views'][0]['cards'][0]['cards']
    row = next(c for c in cards if c.get('type') == 'horizontal-stack' and any(x.get('card', {}).get('type') == 'thermostat' for x in c.get('cards', [])))
    panel = row['cards'][1]
    stack = panel['card']
    original = copy.deepcopy(stack['cards'])
    assert len(original) == 5 and original[1]['heading'] == 'Recliners' and original[3]['heading'] == 'Sofa'
    groups = []
    for title, controls in ((original[1], original[2]), (original[3], original[4])):
        title['card_mod'] = {'style': ''':host {
          --primary-text-color: #202b38;
          --secondary-text-color: #202b38;
          --ha-heading-card-title-color: #202b38;
          --ha-heading-card-subtitle-color: #202b38;
        }
        ha-card { padding: 0 !important; }
        .content, .heading, h1, h2, p { color: #202b38 !important; }
        '''}
        controls['card_mod'] = {'style': ''':host {
          --chip-background: #e3e9f0;
          --chip-border-color: #d2dae4;
          --chip-box-shadow: none;
          --primary-text-color: #202b38;
        }
        ha-card { width: 100%; }
        '''}
        inner = {'type': 'vertical-stack', 'cards': [title, controls], 'card_mod': {'style': ''':host { display: flex; flex: 1; }
          #root { flex: 1; justify-content: space-evenly; gap: 8px; }
        '''}}
        groups.append({'type': 'custom:mod-card', 'card': inner, 'card_mod': {'style': ''':host { display: flex; flex: 1; min-height: 100px; }
          ha-card {
            display: flex;
            flex: 1;
            box-sizing: border-box;
            padding: 8px;
            background: #f3f5f8;
            color: #202b38;
            border: 1px solid #dce2ea;
            border-radius: 12px;
            box-shadow: none;
            --primary-text-color: #202b38;
            --secondary-text-color: #202b38;
          }
          ha-card > hui-vertical-stack-card { display: flex; flex: 1; min-width: 0; }
        '''}})
    stack['cards'] = [original[0], *groups]
    stack['card_mod'] = {'style': ''':host { display: flex; flex: 1; min-height: 0; }
      #root { display: flex; flex-direction: column; flex: 1; width: 100%; gap: 10px; }
      #root > hui-heading-card { flex: 0 0 auto; }
      #root > mod-card { display: flex; flex: 1; margin: 0 !important; }
    '''}
    panel['card_mod']['style'] = panel['card_mod']['style'].replace('display: block;\n          flex: 1;', 'display: flex;\n          flex: 1;')
    # Assert that the six existing control actions survive exactly.
    assert [c['chips'] for c in (original[2], original[4])] == [g['card']['cards'][1]['chips'] for g in groups]
    return config

runner.align = group_fans
if __name__ == '__main__':
    runner.asyncio.run(runner.main())
