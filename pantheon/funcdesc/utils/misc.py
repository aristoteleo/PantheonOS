
class CreateByGetItem(type):
    def __getitem__(self, args):
        if isinstance(args, tuple):
            return self(*args)
        else:
            return self(args)
