# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Action Executor                    ║
# ║  kb2040 | ch9329 | print                            ║
# ╚══════════════════════════════════════════════════════╝

import config


class ActionExecutor:
    def __init__(self):
        self.mode     = config.ACTION_OUTPUT.lower()
        self.platform = config.PLATFORM_TARGET.lower()
        self._hid     = None   # KB2040Serial or CH9329Serial

        if self.mode == 'kb2040':
            self._init_kb2040()
        elif self.mode == 'ch9329':
            self._init_ch9329()
        # 'print' needs no init

        print(f'[ACTION] mode:{self.mode}  platform:{self.platform}')

    def _init_kb2040(self):
        try:
            from uart.kb2040_packer import KB2040Serial
            self._hid = KB2040Serial()
            if not self._hid.is_connected:
                print('[ACTION] KB2040 not connected — falling back to print')
                self.mode = 'print'
        except Exception as e:
            print(f'[ACTION] KB2040 init failed: {e} — falling back to print')
            self.mode = 'print'

    def _init_ch9329(self):
        try:
            from uart.ch9329_packer import CH9329Serial
            self._hid = CH9329Serial()
            if not self._hid.is_connected:
                print('[ACTION] CH9329 not connected — falling back to print')
                self.mode = 'print'
        except Exception as e:
            print(f'[ACTION] CH9329 init failed: {e} — falling back to print')
            self.mode = 'print'

    def execute(self, action_dict: dict):
        if not action_dict:
            return
        if self.mode == 'print':
            self._print_action(action_dict)
        else:
            self._execute_hid(action_dict)

    def _print_action(self, ad: dict):
        key   = ad.get('key')
        click = ad.get('click')
        gp    = ad.get('gamepad') or {}
        src   = ad.get('source', '?')
        parts = [f'src:{src}']
        if key:   parts.append(f'key:{key}')
        if click: parts.append(f'click:{click}')
        if gp and any(gp.values()):
            parts.append(
                f'gp:lx={gp.get("lx",0)} ly={gp.get("ly",0)} '
                f'btn={gp.get("buttons",0):04x}'
            )
        print(f'[ACTION] → {" | ".join(parts)}')

    def _execute_hid(self, ad: dict):
        if self._hid:
            self._hid.send_action(ad, platform=self.platform)

    def release_all(self):
        if self._hid:
            self._hid.release_all()

    def close(self):
        self.release_all()
        if self._hid:
            self._hid.close()
